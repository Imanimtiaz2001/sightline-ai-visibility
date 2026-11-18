import { Recommendation } from '../../core/api/models';
import { highlight } from './opportunity-drawer';
import { briefMarkdown } from './recommendations.page';

describe('AI answer highlighting', () => {
  it('marks brand and competitor names as text segments, never HTML', () => {
    const segments = highlight('Try <b>Boardwise</b> or tasklane today.', 'Tasklane', ['Boardwise']);
    expect(segments.filter((s) => s.kind !== 'plain').map((s) => [s.kind, s.text])).toEqual([
      ['rival', 'Boardwise'],
      ['brand', 'tasklane'],
    ]);
    expect(segments.map((s) => s.text).join('')).toBe('Try <b>Boardwise</b> or tasklane today.');
  });

  it('treats regex characters in names literally', () => {
    expect(highlight('Use monday.com or mondayXcom', 'monday.com', [])).toEqual([
      { text: 'Use ', kind: 'plain' },
      { text: 'monday.com', kind: 'brand' },
      { text: ' or mondayXcom', kind: 'plain' },
    ]);
  });
});

describe('content brief export', () => {
  it('produces a complete markdown brief', () => {
    const rec = {
      title: 'Acme vs Rival', query_text: 'best crm', content_type: 'comparison_page', target_channels: ['organic', 'ai_answers'],
      effort: 'medium', rationale: 'Because.', outline: ['Intro', 'Pricing'], key_points: ['Be fair'], sources_to_earn: ['g2.com'],
    } as unknown as Recommendation;
    const md = briefMarkdown(rec, 'Acme');
    expect(md).toContain('# Acme vs Rival');
    expect(md).toContain('**Format:** Comparison page');
    expect(md).toContain('1. Intro');
    expect(md).toContain('- g2.com');
  });
});
