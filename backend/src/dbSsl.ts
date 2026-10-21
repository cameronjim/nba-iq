import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';
import type pg from 'pg';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// rds certificates chain to amazon's own roots, which are not in node's bundle.
const RDS_CA_PATH = resolve(__dirname, '../certs/rds-global-bundle.pem');

export function sslConfig(url: string | undefined): pg.PoolConfig['ssl'] {
  if (!url || /@(localhost|127\.0\.0\.1)[:/]/.test(url)) return undefined;
  if (/\.rds\.amazonaws\.com[:/]/.test(url)) {
    return { rejectUnauthorized: true, ca: readFileSync(RDS_CA_PATH, 'utf8') };
  }
  return { rejectUnauthorized: true };
}

