import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { APP_CONFIG } from '../config/app-config';
import { newId } from '../observability/ids';
import {
  Brand,
  BrandInput,
  ListResponse,
  Location,
  Meta,
  Overview,
  Page,
  QueryDetail,
  QueryFilter,
  QueryPage,
  Recommendation,
  RecommendationStatus,
  Run,
  RunBrief,
  RunComparison,
} from './models';

/** Shared plumbing: base URL and query-string encoding (arrays repeat the key). */
abstract class ApiBase {
  protected readonly http = inject(HttpClient);
  protected readonly base = inject(APP_CONFIG).apiBaseUrl;

  protected url(path: string): string {
    return `${this.base}${path}`;
  }

  protected params(values: object): HttpParams {
    let params = new HttpParams();
    for (const [key, value] of Object.entries(values)) {
      if (value === undefined || value === null || value === '') continue;
      for (const item of Array.isArray(value) ? value : [value]) params = params.append(key, String(item));
    }
    return params;
  }
}

@Injectable({ providedIn: 'root' })
export class SystemApi extends ApiBase {
  meta(): Observable<Meta> {
    return this.http.get<Meta>(this.url('/meta'));
  }

  locations(): Observable<Location[]> {
    return this.http.get<Location[]>(this.url('/reference/locations'));
  }

  overview(): Observable<Overview> {
    return this.http.get<Overview>(this.url('/overview'));
  }
}

@Injectable({ providedIn: 'root' })
export class BrandsApi extends ApiBase {
  list(): Observable<ListResponse<Brand>> {
    return this.http.get<ListResponse<Brand>>(this.url('/brands'));
  }

  get(id: string): Observable<Brand> {
    return this.http.get<Brand>(this.url(`/brands/${encodeURIComponent(id)}`));
  }

  create(input: BrandInput): Observable<Brand> {
    return this.http.post<Brand>(this.url('/brands'), input);
  }

  update(id: string, input: BrandInput): Observable<Brand> {
    return this.http.put<Brand>(this.url(`/brands/${encodeURIComponent(id)}`), input);
  }

  delete(id: string): Observable<void> {
    return this.http.delete<void>(this.url(`/brands/${encodeURIComponent(id)}`));
  }

  runs(id: string, limit = 50, offset = 0): Observable<Page<RunBrief>> {
    return this.http.get<Page<RunBrief>>(this.url(`/brands/${encodeURIComponent(id)}/runs`), {
      params: this.params({ limit, offset }),
    });
  }
}

@Injectable({ providedIn: 'root' })
export class RunsApi extends ApiBase {
  /**
   * Starts an analysis. The idempotency key is created once per user intent by the
   * caller, so a double click or a retried request can never start two paid runs.
   */
  start(brandId: string, question: string | null, idempotencyKey: string = newId()): Observable<Run> {
    return this.http.post<Run>(
      this.url(`/brands/${encodeURIComponent(brandId)}/runs`),
      question ? { question } : {},
      { headers: new HttpHeaders({ 'Idempotency-Key': idempotencyKey }) },
    );
  }

  get(id: string): Observable<Run> {
    return this.http.get<Run>(this.url(`/runs/${encodeURIComponent(id)}`));
  }

  retry(id: string): Observable<Run> {
    return this.http.post<Run>(this.url(`/runs/${encodeURIComponent(id)}/retry`), {});
  }

  eventsUrl(id: string): string {
    return this.url(`/runs/${encodeURIComponent(id)}/events`);
  }

  exportUrl(id: string, format: 'csv' | 'json' | 'md'): string {
    return this.url(`/runs/${encodeURIComponent(id)}/export?format=${format}`);
  }

  compare(id: string, baseline?: string): Observable<RunComparison> {
    return this.http.get<RunComparison>(this.url(`/runs/${encodeURIComponent(id)}/comparison`), {
      params: this.params({ baseline }),
    });
  }
}

@Injectable({ providedIn: 'root' })
export class InsightsApi extends ApiBase {
  queries(runId: string, filter: QueryFilter = {}): Observable<QueryPage> {
    return this.http.get<QueryPage>(this.url(`/runs/${encodeURIComponent(runId)}/queries`), {
      params: this.params({ limit: 200, ...filter }),
    });
  }

  query(id: string): Observable<QueryDetail> {
    return this.http.get<QueryDetail>(this.url(`/queries/${encodeURIComponent(id)}`));
  }

  recommendations(runId: string, status?: RecommendationStatus[]): Observable<Recommendation[]> {
    return this.http.get<Recommendation[]>(this.url(`/runs/${encodeURIComponent(runId)}/recommendations`), {
      params: this.params({ status }),
    });
  }

  setRecommendationStatus(id: string, status: RecommendationStatus): Observable<Recommendation> {
    return this.http.patch<Recommendation>(this.url(`/recommendations/${encodeURIComponent(id)}`), { status });
  }
}
