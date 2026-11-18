import { FormArray, FormControl, FormGroup } from '@angular/forms';
import { competitorsValidator, domainValidator, normalizeDomain } from './brand-validators';

describe('brand validators', () => {
  it('normalises domains the same way as the API', () => {
    expect(normalizeDomain(' https://www.Example.com/pricing?x=1 ')).toBe('example.com');
    expect(normalizeDomain('sub.example.co.uk:8080')).toBe('sub.example.co.uk');
  });

  it('accepts real domains and rejects junk', () => {
    expect(domainValidator(new FormControl('https://acme.io/'))).toBeNull();
    expect(domainValidator(new FormControl('not a domain'))).toEqual({ domain: true });
    expect(domainValidator(new FormControl(''))).toBeNull(); // "required" handles emptiness
  });

  it('rejects duplicate competitors and the brand itself', () => {
    const row = (name: string, domain: string) =>
      new FormGroup({ name: new FormControl(name), domain: new FormControl(domain) });
    const validator = competitorsValidator(() => 'acme.io', () => 'Acme');
    expect(validator(new FormArray([row('Rival', 'rival.com'), row('Other', 'other.com')]))).toBeNull();
    expect(validator(new FormArray([row('Rival', 'rival.com'), row('Rival 2', 'www.rival.com')]))).toEqual({ duplicateDomain: true });
    expect(validator(new FormArray([row('Acme', 'https://acme.io')]))).toEqual({ ownDomain: true, ownName: true });
  });
});
