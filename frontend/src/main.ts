import { bootstrapApplication } from '@angular/platform-browser';
import { App } from './app/app';
import { buildAppConfig } from './app/app.config';
import { loadConfig } from './app/core/config/app-config';

// Runtime configuration is fetched before bootstrap so one build serves every environment.
loadConfig()
  .then((config) => bootstrapApplication(App, buildAppConfig(config)))
  .catch((err: unknown) => {
    console.error(err);
    document.body.textContent = 'Sightline could not start. Reload the page; if it keeps happening, contact support.';
  });
