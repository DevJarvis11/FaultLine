// HTTP flood against one shop endpoint (benchmark scenarios 4 and 6).
// Constant arrival rate per container, one path, one uniform non-browser User-Agent.
// Rate and duration are capped so the laptop cannot be exhausted.
import http from 'k6/http';
import { labTarget, boundedInt, oneLineSummary } from './lib/guard.js';

const target = labTarget();
const RATE = boundedInt('RATE', 5, 1, 20); // requests per second per container
const DURATION_S = boundedInt('DURATION_S', 30, 5, 120);
const PATH = __ENV.FLOOD_PATH || '/';
const UA = __ENV.FLOOD_UA || 'Go-http-client/1.1';

if (!/^\/[A-Za-z0-9/_\-.]*$/.test(PATH)) {
  throw new Error(`refusing to run: FLOOD_PATH ${PATH} must be a plain absolute path`);
}

export const options = {
  hosts: target.hosts,
  discardResponseBodies: true,
  scenarios: {
    flood: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: `${DURATION_S}s`,
      preAllocatedVUs: Math.min(RATE, 10),
      maxVUs: 2 * RATE,
    },
  },
};

const params = { headers: { 'User-Agent': UA, Accept: '*/*' }, timeout: '5s' };

export default function () {
  http.get(`${target.base}${PATH}`, params);
}

export function handleSummary(data) {
  return oneLineSummary('flood', data);
}
