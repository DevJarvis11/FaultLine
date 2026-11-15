// Organic browsing sessions against the lab shop (benchmark scenario 5: flash sale).
// Each VU loops over shopper sessions: home, several product pages, maybe add to
// cart, view cart, occasional checkout, with human think times.
import http from 'k6/http';
import { check, sleep } from 'k6';
import { labTarget, boundedInt, oneLineSummary } from './lib/guard.js';

const target = labTarget();
const VUS = boundedInt('VUS', 2, 1, 5);
const DURATION_S = boundedInt('DURATION_S', 90, 10, 600);

export const options = {
  hosts: target.hosts,
  discardResponseBodies: true,
  scenarios: {
    shoppers: { executor: 'constant-vus', vus: VUS, duration: `${DURATION_S}s`, gracefulStop: '10s' },
  },
};

// Product IDs from the live shop home page (Online Boutique v0.10.7 catalog).
const PRODUCTS = [
  'OLJCESPC7Z', '66VCHSJNUP', '1YMWWN1N4O', 'L9ECAV7KIM', '2ZYFJ3GM2N',
  '0PUK6V6EV0', 'LS4PSXUNUM', '9SIQT8TOJO', '6E92ZMYYFZ',
];
// Whitelisted currencies in src/frontend/main.go at v0.10.7.
const CURRENCIES = ['USD', 'EUR', 'CAD', 'JPY', 'GBP', 'TRY'];

// Common desktop and mobile browser User-Agent strings.
const USER_AGENTS = [
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Safari/605.1.15',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:144.0) Gecko/20100101 Firefox/144.0',
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
  'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:144.0) Gecko/20100101 Firefox/144.0',
  'Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1',
  'Mozilla/5.0 (iPad; CPU OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1',
  'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36',
  'Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36 SamsungBrowser/28.0',
];

const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
const think = (min, max) => sleep(min + Math.random() * (max - min));
const shuffled = (arr) => arr.map((v) => [Math.random(), v]).sort((a, b) => a[0] - b[0]).map((p) => p[1]);

function headers(ua, referer) {
  const h = {
    'User-Agent': ua,
    Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
  };
  if (referer) h.Referer = referer;
  return { headers: h, tags: { name: 'page' } };
}

function get(path, ua, referer) {
  const res = http.get(`${target.base}${path}`, headers(ua, referer));
  check(res, { 'status < 500': (r) => r.status < 500 });
  return `${target.base}${path}`;
}

function post(path, form, ua, referer) {
  const res = http.post(`${target.base}${path}`, form, headers(ua, referer));
  check(res, { 'status < 500': (r) => r.status < 500 });
}

function checkoutForm() {
  // Default test values pre-filled in templates/cart.html at v0.10.7.
  return {
    email: 'someone@example.com',
    street_address: '1600 Amphitheatre Parkway',
    zip_code: '94043',
    city: 'Mountain View',
    state: 'CA',
    country: 'United States',
    credit_card_number: '4432801561520454',
    credit_card_expiration_month: '1',
    credit_card_expiration_year: String(new Date().getFullYear() + 1),
    credit_card_cvv: '672',
  };
}

export default function () {
  // One browser per container slot; VUs in a container may use different browsers.
  const gen = Number(__ENV.GEN_INDEX || '0');
  const ua = USER_AGENTS[((gen - 1) * VUS + __VU - 1 + USER_AGENTS.length) % USER_AGENTS.length];
  http.cookieJar().clear(target.base); // new shopper session

  let ref = get('/', ua, null);
  think(1, 4);
  if (Math.random() < 0.2) {
    post('/setCurrency', { currency_code: pick(CURRENCIES) }, ua, ref);
    think(0.5, 2);
  }
  const views = 3 + Math.floor(Math.random() * 4);
  const browsed = shuffled(PRODUCTS).slice(0, views);
  for (const id of browsed) {
    ref = get(`/product/${id}`, ua, ref);
    think(2, 6);
  }
  if (Math.random() < 0.6) {
    const id = pick(browsed);
    post('/cart', { product_id: id, quantity: String(1 + Math.floor(Math.random() * 3)) }, ua, `${target.base}/product/${id}`);
    think(1, 3);
    ref = get('/cart', ua, ref);
    think(2, 5);
    if (Math.random() < 0.25) {
      post('/cart/checkout', checkoutForm(), ua, ref);
      think(2, 4);
    }
  } else if (Math.random() < 0.3) {
    get('/cart', ua, ref);
    think(1, 3);
  }
  think(1, 5); // gap before the next shopper session
}

export function handleSummary(data) {
  return oneLineSummary('organic', data);
}
