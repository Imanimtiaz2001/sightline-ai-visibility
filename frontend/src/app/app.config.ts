import { provideHttpClient, withFetch, withInterceptors } from '@angular/common/http';
import { ApplicationConfig, ErrorHandler, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideRouter, withComponentInputBinding, withInMemoryScrolling } from '@angular/router';
import { routes } from './app.routes';
import { APP_CONFIG, AppConfig } from './core/config/app-config';
import { correlationInterceptor, errorInterceptor } from './core/http/interceptors';
import { GlobalErrorHandler } from './core/observability/global-error-handler';

export function buildAppConfig(config: AppConfig): ApplicationConfig {
  return {
    providers: [
      provideBrowserGlobalErrorListeners(),
      { provide: APP_CONFIG, useValue: config },
      { provide: ErrorHandler, useClass: GlobalErrorHandler },
      provideRouter(routes, withComponentInputBinding(), withInMemoryScrolling({ scrollPositionRestoration: 'top' })),
      provideHttpClient(withFetch(), withInterceptors([correlationInterceptor, errorInterceptor])),
    ],
  };
}
