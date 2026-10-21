import { describe, it, expect } from 'vitest';
import { sslConfig } from '../../src/dbSsl.js';

describe('sslConfig', () => {
  it('uses no tls for a local database', () => {
    // act + assert
    expect(sslConfig('postgresql://u:p@localhost:5432/db')).toBeUndefined();
  });

  it('verifies against the bundled rds certificate chain for an rds host', () => {
    // act
    const ssl = sslConfig('postgresql://u:p@nbaiq.cluster-abc.us-east-1.rds.amazonaws.com:5432/nbaiq');

    // assert
    expect(ssl).toMatchObject({ rejectUnauthorized: true });
    expect(String((ssl as { ca: string }).ca)).toContain('BEGIN CERTIFICATE');
  });

  it('verifies against the system roots for any other remote host', () => {
    // act + assert
    expect(sslConfig('postgresql://u:p@ep-x.neon.tech/db')).toEqual({ rejectUnauthorized: true });
  });
});
