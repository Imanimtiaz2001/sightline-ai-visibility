import { Routes } from '@angular/router';
import { Shell } from './layout/shell';

/** Every feature is lazy-loaded; the shell (navigation, banners, toasts) is the only eager UI. */
export const routes: Routes = [
  {
    path: '',
    component: Shell,
    children: [
      {
        path: '',
        title: 'Portfolio – Sightline',
        loadComponent: () => import('./features/portfolio/portfolio.page').then((m) => m.PortfolioPage),
      },
      {
        path: 'brands/new',
        title: 'Add a brand – Sightline',
        loadComponent: () => import('./features/brand-form/brand-form.page').then((m) => m.BrandFormPage),
      },
      {
        path: 'brands/:id/edit',
        title: 'Edit brand – Sightline',
        loadComponent: () => import('./features/brand-form/brand-form.page').then((m) => m.BrandFormPage),
      },
      {
        path: 'brands/:id',
        loadChildren: () => import('./features/brand/brand.routes').then((m) => m.BRAND_ROUTES),
      },
      {
        path: 'runs/:id',
        title: 'Analysis – Sightline',
        loadComponent: () => import('./features/run/run.page').then((m) => m.RunPage),
      },
      {
        path: '**',
        title: 'Not found – Sightline',
        loadComponent: () => import('./features/not-found.page').then((m) => m.NotFoundPage),
      },
    ],
  },
];
