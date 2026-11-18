import { Routes } from '@angular/router';
import { BrandLayout } from './brand.layout';

export const BRAND_ROUTES: Routes = [
  {
    path: '',
    component: BrandLayout,
    children: [
      {
        path: '',
        title: 'Brand overview – Sightline',
        loadComponent: () => import('./overview.page').then((m) => m.BrandOverviewPage),
      },
      {
        path: 'opportunities',
        title: 'Opportunities – Sightline',
        loadComponent: () => import('./opportunities.page').then((m) => m.OpportunitiesPage),
      },
      {
        path: 'recommendations',
        title: 'Recommendations – Sightline',
        loadComponent: () => import('./recommendations.page').then((m) => m.RecommendationsPage),
      },
      {
        path: 'history',
        title: 'History – Sightline',
        loadComponent: () => import('./history.page').then((m) => m.HistoryPage),
      },
    ],
  },
];
