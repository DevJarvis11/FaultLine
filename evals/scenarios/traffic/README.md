# Traffic generators (benchmark scenarios 4, 5, 6)

Simulated traffic against this lab only: `http://shop.faultline.lab:8000`.

```
./run.sh organic|flood [-n containers] [-d duration_s] [-v vus] [-r rate] [-p flood_path]
```

- Image: `grafana/k6:2.4.0` (Docker Hub tag, matches GitHub release v2.4.0).
- Network: `fl-traffic`, bridge, subnet `172.31.250.0/24`, gateway `172.31.250.1`.
  Created once (refuses if the subnet overlaps 10.42/16, 10.43/16, 192.168.1.0/24, any
  Docker network or any host route) and kept for reuse. Each container gets its own IP,
  which Traefik logs as `ClientHost`, so a run can be filtered with
  `ClientHost=~"172\\.31\\.250\\..+"` plus the run's time window.
- Containers: `-n` 1..40 (default 20), 96 MiB and 0.5 CPU each, labelled
  `faultline.run=<run id>`, always removed at the end (also on Ctrl-C).
- The runner prints per-container request counts, the docker memory peak and the
  lowest node `MemAvailable` seen during the run.

| Script | Parameter | Default | Bounds | Meaning |
| --- | --- | --- | --- | --- |
| organic.js | `-d` DURATION_S | 90 | 10..600 | run length (plus up to 10 s graceful stop) |
| organic.js | `-v` VUS | 2 | 1..5 | concurrent shoppers per container |
| flood.js | `-d` DURATION_S | 30 | 5..120 | run length |
| flood.js | `-r` RATE | 5 | 1..20 | requests per second per container |
| flood.js | `-p` FLOOD_PATH | `/` | plain path | the one endpoint hit |

**organic.js**: shopper sessions: home, 3 to 6 product pages, 60% add to cart and view
cart, a quarter of those check out with the shop's default form values, 20% change
currency; 0.5 to 6 s think times; new cookie session per loop; 12 real browser
User-Agents spread across containers and VUs.

**flood.js**: constant arrival rate to one path with one uniform non-browser
User-Agent (`Go-http-client/1.1`, override with env `FLOOD_UA`).

## Safety guard

- `run.sh` refuses unless `shop.faultline.lab` resolves to an IPv4 address assigned to
  `docker0` or a `br-*` interface on this machine.
- Both scripts refuse unless `TARGET_HOST` is `shop.faultline.lab` and `SHOP_IP` is in
  172.16.0.0/12, and pin that mapping with k6 `options.hosts`. Out-of-bounds parameters
  abort the test before any request is sent.

## Shape check in Loki

```
sel = {namespace="ingress", container="traefik", log_type="access"} | json | ClientHost=~"172\\.31\\.250\\..+"
count(sum by (ClientHost) (count_over_time(<sel> [W])))                               # distinct clients
sum by (ClientHost) (count_over_time(<sel> [W]))                                      # requests per client
count by (ClientHost) (sum by (ClientHost, RequestPath) (count_over_time(<sel> [W]))) # paths per client
count(sum by (request_User_Agent) (count_over_time(<sel> [W])))                       # distinct UAs
```

Evaluate each as an instant query at the run's end time with `W` covering the run.
