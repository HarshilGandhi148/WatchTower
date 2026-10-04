import { object, text } from './adapter';

export function recommendationSummary(value: string): string {
  return value.replace(
    'Continue weather monitoring and monitor nearby aircraft; no material weather impact is identified for this window.',
    'Continue monitoring KDTW weather and nearby aircraft.',
  );
}

/** Present retained facts first; preserve conditional forecast labels and hazards. */
export function weatherSummary(v: Record<string, unknown>): string {
  const labels: Record<string, string> = {
    wind_speed_kt: 'wind',
    wind_gust_kt: 'gusts',
    wind_direction_deg: 'wind direction',
    visibility_m: 'visibility',
    ceiling_ft: 'ceiling',
    temperature_c: 'temperature',
  };
  const facts = Array.isArray(v.weather_facts)
    ? v.weather_facts.map(object)
    : [];
  const selected = new Map<string, Record<string, unknown>>();
  for (const fact of facts) {
    if (
      !labels[String(fact.name)] ||
      typeof fact.value !== 'number' ||
      !Number.isFinite(fact.value)
    )
      continue;
    const key = `${fact.basis}:${fact.name}`;
    const previous = selected.get(key);
    if (!previous || String(fact.start_utc) > String(previous.start_utc))
      selected.set(key, fact);
  }
  const observations = [...selected.values()].filter(
    (f) => f.basis === 'observed',
  );
  const shown = observations.length
    ? observations
    : [...selected.values()].slice(0, 5);
  const values = shown.map((f) => {
    const bound =
      f.bound === 'greater_than' ? '>' : f.bound === 'less_than' ? '<' : '';
    const prefix =
      f.basis === 'observed'
        ? ''
        : f.basis === 'conditional'
          ? 'conditional forecast '
          : 'forecast ';
    return `${prefix}${labels[String(f.name)]} ${bound}${Number(f.value).toLocaleString('en-US', { maximumFractionDigits: 2 })} ${text(f.unit)}`;
  });
  const hazards = (Array.isArray(v.hazards) ? v.hazards.map(object) : []).map(
    (h) =>
      `${h.basis === 'observed' ? 'Observed' : h.basis === 'conditional' ? 'Potential forecast' : 'Forecast'} ${text(h.kind).replaceAll('_', ' ')} concern${typeof h.probability_percent === 'number' ? ` (${h.probability_percent}% probability)` : ''}`,
  );
  const lead = values.length
    ? `KDTW: ${values.join('; ')}.`
    : 'Preparing the KDTW weather review as observations update.';
  return `${lead} ${hazards.length ? [...new Set(hazards)].join('; ') + '.' : v.status === 'no_material_impact' ? 'Continue weather monitoring for this window.' : 'Using the available observations for this window.'}`;
}
