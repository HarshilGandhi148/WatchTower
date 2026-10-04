import type { FlowPoint, Projection } from '../live/types';

export const finite = (value: number | null | undefined): value is number =>
  typeof value === 'number' && Number.isFinite(value);
export function modelSample(projection: Projection, minute: number) {
  const sampleMinute =
    projection.baseline
      .filter(
        (point) => point.minute >= 0 && point.minute <= Math.min(15, minute),
      )
      .sort((a, b) => b.minute - a.minute)[0]?.minute ?? 0;
  return {
    minute: sampleMinute,
    baseline: projection.baseline.find(
      (point) => point.minute === sampleMinute,
    ),
    proposed: projection.proposed.find(
      (point) => point.minute === sampleMinute,
    ),
  };
}
export const displayValue = (value: number | null | undefined, unit: string) =>
  finite(value)
    ? `${new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(value)} ${unit}`.trim()
    : 'Not estimable with available data';
export function horizonOutcomes(projection: Projection) {
  const baseline = projection.baseline.find((point) => point.minute === 15);
  const proposed = projection.proposed.find((point) => point.minute === 15);
  const metrics: {
    key: keyof Omit<FlowPoint, 'minute'>;
    label: string;
    unit: string;
  }[] = [
    { key: 'runwayQueue', label: 'Runway queue', unit: 'aircraft' },
    {
      key: 'totalWaitingAircraftMinutes',
      label: 'Total waiting',
      unit: 'aircraft-min',
    },
    { key: 'departures', label: 'Departures', unit: 'aircraft' },
  ];
  const densityOnly = metrics.every(
    ({ key }) => !finite(baseline?.[key]) && !finite(proposed?.[key]),
  );
  const shown = densityOnly
    ? [
        {
          key: 'pressure' as const,
          label: 'Traffic pressure',
          unit: projection.pressureUnit,
        },
      ]
    : metrics;
  return {
    densityOnly,
    metrics: shown.map((metric) => ({
      ...metric,
      baseline: baseline?.[metric.key],
      proposed: proposed?.[metric.key],
      change:
        finite(baseline?.[metric.key]) && finite(proposed?.[metric.key])
          ? proposed![metric.key]! - baseline![metric.key]!
          : null,
    })),
  };
}
