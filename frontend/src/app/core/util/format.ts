import { Pipe, PipeTransform } from '@angular/core';

const compact = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 });
const whole = new Intl.NumberFormat('en');

export function formatVolume(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'Unknown';
  return value >= 10_000 ? compact.format(value) : whole.format(value);
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '–';
  return `${Math.round(value * 100)}%`;
}

export function formatScore(value: number | null | undefined): string {
  return value === null || value === undefined ? '–' : String(Math.round(value));
}

export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return 'never';
  const seconds = Math.round((now - new Date(iso).getTime()) / 1000);
  if (seconds < 45) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? '' : 's'} ago`;
  return new Date(iso).toLocaleDateString('en', { day: 'numeric', month: 'short', year: 'numeric' });
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '–';
  return new Date(iso).toLocaleString('en', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '–';
  if (seconds < 60) return `${Math.round(seconds)} s`;
  return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .map((p) => p[0] ?? '')
    .join('')
    .slice(0, 2)
    .toUpperCase();
}

@Pipe({ name: 'volume' })
export class VolumePipe implements PipeTransform {
  transform = formatVolume;
}

@Pipe({ name: 'pct' })
export class PercentPipe implements PipeTransform {
  transform = formatPercent;
}

@Pipe({ name: 'score' })
export class ScorePipe implements PipeTransform {
  transform = formatScore;
}

@Pipe({ name: 'ago' })
export class AgoPipe implements PipeTransform {
  transform(value: string | null | undefined): string {
    return relativeTime(value);
  }
}

@Pipe({ name: 'when' })
export class WhenPipe implements PipeTransform {
  transform = formatDate;
}

@Pipe({ name: 'duration' })
export class DurationPipe implements PipeTransform {
  transform = formatDuration;
}

@Pipe({ name: 'label' })
export class LabelPipe implements PipeTransform {
  transform(value: string | null | undefined, map: Record<string, string>): string {
    if (!value) return '–';
    return map[value] ?? value.replace(/_/g, ' ');
  }
}
