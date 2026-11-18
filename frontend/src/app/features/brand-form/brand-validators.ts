import { AbstractControl, FormArray, ValidationErrors, ValidatorFn } from '@angular/forms';

const DOMAIN = /^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;

/** Same normalisation the API applies, so client and server agree on what a domain is. */
export function normalizeDomain(value: string): string {
  let v = (value ?? '').trim().toLowerCase();
  v = v.replace(/^[a-z]+:\/\//, '').split(/[/?#]/)[0].replace(/^www\./, '');
  return v.split(':')[0];
}

export const domainValidator: ValidatorFn = (control: AbstractControl<string>): ValidationErrors | null => {
  const raw = control.value ?? '';
  if (!raw.trim()) return null;
  return DOMAIN.test(normalizeDomain(raw)) ? null : { domain: true };
};

/** Competitors must differ from each other and from the brand itself. */
export function competitorsValidator(brandDomain: () => string, brandName: () => string): ValidatorFn {
  return (control: AbstractControl): ValidationErrors | null => {
    const rows = (control as FormArray).controls.map((c) => c.value as { name: string; domain: string });
    const domains = rows.map((r) => normalizeDomain(r.domain)).filter(Boolean);
    const names = rows.map((r) => (r.name ?? '').trim().toLowerCase()).filter(Boolean);
    const errors: ValidationErrors = {};
    if (new Set(domains).size !== domains.length) errors['duplicateDomain'] = true;
    if (new Set(names).size !== names.length) errors['duplicateName'] = true;
    const own = normalizeDomain(brandDomain());
    if (own && domains.includes(own)) errors['ownDomain'] = true;
    const ownName = brandName().trim().toLowerCase();
    if (ownName && names.includes(ownName)) errors['ownName'] = true;
    return Object.keys(errors).length ? errors : null;
  };
}
