import { SimRecommendationConfidence } from '../types/draft';

export function confidencePresentation(
  confidence?: SimRecommendationConfidence,
): { label: string; color: string; detail: string } | null {
  if (!confidence) return null;
  const labels = {
    near_tie: 'Near tie',
    slight_edge: 'Slight edge',
    strong_edge: 'Strong edge',
    only_option: 'Only option',
  };
  const colors = {
    near_tie: 'gray',
    slight_edge: 'blue',
    strong_edge: 'green',
    only_option: 'purple',
  };
  const gap = confidence.gap == null ? '' : `+${confidence.gap.toFixed(1)} VAL`;
  const win = `${Math.round(confidence.win_pct * 100)}% of paired rollouts`;
  return {
    label: labels[confidence.label],
    color: colors[confidence.label],
    detail: [gap, win].filter(Boolean).join(' · '),
  };
}
