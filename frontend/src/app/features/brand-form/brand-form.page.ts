import { ChangeDetectionStrategy, Component, OnInit, computed, inject, input, signal } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormArray, FormBuilder, FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { startWith } from 'rxjs';
import { ApiError } from '../../core/api/api-error';
import { BrandsApi } from '../../core/api/api.services';
import { Brand, BrandInput } from '../../core/api/models';
import { MetaStore, PortfolioStore, ToastService } from '../../core/state/stores';
import { StatePanel } from '../../shared/state-panel';
import { competitorsValidator, domainValidator, normalizeDomain } from './brand-validators';

type CompetitorForm = FormGroup<{ name: FormControl<string>; domain: FormControl<string> }>;

const MAX_COMPETITORS = 6;
const MAX_TOPICS = 10;

@Component({
  selector: 'app-brand-form-page',
  imports: [ReactiveFormsModule, RouterLink, StatePanel],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './brand-form.page.html',
  styleUrl: './brand-form.page.scss',
})
export class BrandFormPage implements OnInit {
  /** Route param (bound via withComponentInputBinding); absent when creating. */
  readonly id = input<string>();

  private readonly fb = inject(FormBuilder).nonNullable;
  private readonly api = inject(BrandsApi);
  private readonly router = inject(Router);
  private readonly portfolio = inject(PortfolioStore);
  private readonly toasts = inject(ToastService);
  protected readonly meta = inject(MetaStore);

  protected readonly loading = signal(false);
  protected readonly loadError = signal<unknown>(null);
  protected readonly saving = signal(false);
  protected readonly confirmDelete = signal(false);
  protected readonly serverError = signal<ApiError | null>(null);
  protected readonly editing = computed(() => !!this.id());

  protected readonly form = this.fb.group({
    name: ['', [Validators.required, Validators.maxLength(80)]],
    domain: ['', [Validators.required, domainValidator]],
    market: ['', [Validators.required, Validators.minLength(3), Validators.maxLength(200)]],
    description: ['', [Validators.maxLength(1000)]],
    location_code: [2840, [Validators.required]],
    language_code: ['en', [Validators.required]],
    seed_topics: this.fb.array<FormControl<string>>([]),
    competitors: this.fb.array<CompetitorForm>([]),
  });
  protected readonly topicDraft = new FormControl('', { nonNullable: true, validators: [Validators.maxLength(80)] });

  private readonly locationCode = toSignal(
    this.form.controls.location_code.valueChanges.pipe(startWith(this.form.controls.location_code.value)),
    { initialValue: 2840 },
  );
  protected readonly languages = computed(
    () => this.meta.locations().find((l) => l.code === Number(this.locationCode()))?.languages ?? [{ code: 'en', name: 'English' }],
  );

  get competitors(): FormArray<CompetitorForm> {
    return this.form.controls.competitors;
  }

  get topics(): FormArray<FormControl<string>> {
    return this.form.controls.seed_topics;
  }

  constructor() {
    this.competitors.addValidators(
      competitorsValidator(
        () => this.form.controls.domain.value,
        () => this.form.controls.name.value,
      ),
    );
    // Keep the language valid when the market changes.
    this.form.controls.location_code.valueChanges.pipe(takeUntilDestroyed()).subscribe((code) => {
      const langs = this.meta.locations().find((l) => l.code === Number(code))?.languages ?? [];
      if (langs.length && !langs.some((l) => l.code === this.form.controls.language_code.value)) {
        this.form.controls.language_code.setValue(langs[0].code);
      }
    });
    // Brand domain/name changes can make competitor rows invalid.
    this.form.controls.domain.valueChanges.pipe(takeUntilDestroyed()).subscribe(() => this.competitors.updateValueAndValidity());
    this.form.controls.name.valueChanges.pipe(takeUntilDestroyed()).subscribe(() => this.competitors.updateValueAndValidity());
  }

  ngOnInit(): void {
    this.meta.loadLocations().subscribe();
    const id = this.id();
    if (id) {
      this.loading.set(true);
      this.api.get(id).subscribe({
        next: (brand) => {
          this.patch(brand);
          this.loading.set(false);
        },
        error: (e: unknown) => {
          this.loadError.set(e);
          this.loading.set(false);
        },
      });
    } else {
      this.addCompetitor();
    }
  }

  protected addCompetitor(): void {
    if (this.competitors.length >= MAX_COMPETITORS) return;
    this.competitors.push(
      this.fb.group({
        name: ['', [Validators.required, Validators.maxLength(80)]],
        domain: ['', [Validators.required, domainValidator]],
      }),
    );
  }

  protected removeCompetitor(index: number): void {
    this.competitors.removeAt(index);
  }

  protected addTopic(event?: Event): void {
    event?.preventDefault();
    const value = this.topicDraft.value.trim().replace(/\s+/g, ' ');
    if (value.length < 2 || this.topicDraft.invalid || this.topics.length >= MAX_TOPICS) return;
    if (!this.topics.value.some((t) => t.toLowerCase() === value.toLowerCase())) {
      this.topics.push(this.fb.control(value));
    }
    this.topicDraft.setValue('');
  }

  protected removeTopic(index: number): void {
    this.topics.removeAt(index);
  }

  protected showError(control: { invalid: boolean; touched: boolean; dirty: boolean }): boolean {
    return control.invalid && (control.touched || control.dirty);
  }

  protected readonly maxCompetitors = MAX_COMPETITORS;
  protected readonly maxTopics = MAX_TOPICS;

  protected submit(): void {
    this.addTopic();
    this.form.markAllAsTouched();
    if (this.form.invalid || this.saving()) return;
    const raw = this.form.getRawValue();
    const body: BrandInput = {
      ...raw,
      location_code: Number(raw.location_code),
      domain: normalizeDomain(raw.domain),
      competitors: raw.competitors.map((c) => ({ name: c.name.trim(), domain: normalizeDomain(c.domain) })),
    };
    this.saving.set(true);
    this.serverError.set(null);
    const id = this.id();
    const request = id ? this.api.update(id, body) : this.api.create(body);
    request.subscribe({
      next: (brand) => {
        this.portfolio.upsert(brand);
        this.toasts.show(id ? 'Brand saved' : `${brand.name} added. Start its first analysis when you are ready.`, 'success');
        void this.router.navigate(['/brands', brand.id]);
      },
      error: (e: unknown) => {
        const error = ApiError.from(e);
        this.saving.set(false);
        this.serverError.set(error);
        this.applyServerErrors(error);
      },
    });
  }

  protected remove(): void {
    const id = this.id();
    if (!id) return;
    this.api.delete(id).subscribe({
      next: () => {
        this.portfolio.remove(id);
        this.toasts.show('Brand deleted', 'success');
        void this.router.navigate(['/']);
      },
      error: (e: unknown) => this.toasts.error(e),
    });
  }

  /** Map `competitors.1.domain`-style server field paths back onto the controls. */
  private applyServerErrors(error: ApiError): void {
    for (const [path, message] of Object.entries(error.fieldErrors)) {
      const control = this.form.get(path.split('.'));
      control?.setErrors({ server: message });
      control?.markAsTouched();
    }
  }

  private patch(brand: Brand): void {
    this.form.patchValue({
      name: brand.name,
      domain: brand.domain,
      market: brand.market,
      description: brand.description,
      location_code: brand.location_code,
      language_code: brand.language_code,
    });
    this.topics.clear();
    brand.seed_topics.forEach((t) => this.topics.push(this.fb.control(t)));
    this.competitors.clear();
    brand.competitors.forEach((c) => {
      this.addCompetitor();
      this.competitors.at(-1).setValue({ name: c.name, domain: c.domain });
    });
    this.form.markAsPristine();
  }
}
