// Lab-only safety guard shared by the k6 scripts. Runs in the init context, so a
// failed check aborts the test before any request is sent.

export const LAB_HOST = 'shop.faultline.lab';

// Docker bridge addresses on this machine live in 172.16.0.0/12 (docker0 is 172.19.0.1).
// The runner additionally checks the IP is assigned to a local docker bridge interface.
function isDockerBridgeIp(ip) {
  const m = /^172\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(ip || '');
  if (!m) return false;
  const second = Number(m[1]);
  return second >= 16 && second <= 31 && Number(m[2]) <= 255 && Number(m[3]) <= 255;
}

export function labTarget() {
  const host = __ENV.TARGET_HOST || LAB_HOST;
  const ip = __ENV.SHOP_IP;
  const port = Number(__ENV.TARGET_PORT || '8000');
  if (host !== LAB_HOST) {
    throw new Error(`refusing to run: TARGET_HOST must be ${LAB_HOST}, got ${host}`);
  }
  if (!isDockerBridgeIp(ip)) {
    throw new Error(`refusing to run: SHOP_IP ${ip} is not a docker bridge address (172.16.0.0/12)`);
  }
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`refusing to run: bad TARGET_PORT ${__ENV.TARGET_PORT}`);
  }
  return { base: `http://${LAB_HOST}:${port}`, hosts: { [LAB_HOST]: ip } };
}

// Parse a bounded numeric parameter; out-of-range values abort the test.
export function boundedInt(name, def, min, max) {
  const raw = __ENV[name];
  const v = raw === undefined || raw === '' ? def : Number(raw);
  if (!Number.isInteger(v) || v < min || v > max) {
    throw new Error(`refusing to run: ${name}=${raw} must be an integer in [${min}, ${max}]`);
  }
  return v;
}

export function oneLineSummary(kind, data) {
  const m = data.metrics || {};
  const reqs = m.http_reqs ? m.http_reqs.values.count : 0;
  const failed = m.http_req_failed ? m.http_req_failed.values.rate : 0;
  const gen = __ENV.GEN_INDEX || '?';
  return { stdout: `${kind} gen=${gen} reqs=${reqs} failed_rate=${failed.toFixed(3)}\n` };
}
