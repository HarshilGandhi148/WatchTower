import { distanceFromDtwNm } from '../../components/dashboard/geography';
import { object } from './adapter';

export interface ReadableEvidence {
  facts: { label: string; value: string }[];
  planes: {
    id: string;
    label: string;
    altitude: string;
    speed: string;
    heading: string;
    distance: string;
  }[];
}
const number = (value: number) =>
  value.toLocaleString('en-US', { maximumFractionDigits: 1 });
export function readableEvidence(value: unknown): ReadableEvidence {
  const facts: ReadableEvidence['facts'] = [],
    planes: ReadableEvidence['planes'] = [];
  try {
    const root = object(value);
    const cutoff = Date.parse(
      String(root.cutoff_at_utc ?? root.window_start_utc),
    );
    const radius = Number(object(root.configuration).radius_nm ?? 40);
    for (const item of Array.isArray(root.batches) ? root.batches : []) {
      const payload = object(object(item).payload);
      const source = object(payload.snapshot ?? payload);
      if (payload.provider === 'adsb_lol') {
        const rows = (
          Array.isArray(source.observations) ? source.observations : []
        )
          .map(object)
          .filter(
            (r) =>
              typeof r.latitude_deg === 'number' &&
              typeof r.longitude_deg === 'number' &&
              cutoff - Date.parse(String(r.observed_at_utc)) >= 0 &&
              cutoff - Date.parse(String(r.observed_at_utc)) <= 30000 &&
              distanceFromDtwNm(
                Number(r.latitude_deg),
                Number(r.longitude_deg),
              ) <= radius,
          );
        facts.push(
          {
            label: 'Nearby aircraft',
            value: `${rows.length} observed within ${radius} nautical miles of KDTW`,
          },
          {
            label: 'In the air',
            value: `${rows.filter((r) => r.on_ground === false).length} aircraft`,
          },
          {
            label: 'On the ground',
            value: `${rows.filter((r) => r.on_ground === true).length} aircraft`,
          },
        );
        rows.sort(
          (a, b) =>
            distanceFromDtwNm(Number(a.latitude_deg), Number(a.longitude_deg)) -
            distanceFromDtwNm(Number(b.latitude_deg), Number(b.longitude_deg)),
        );
        planes.push(
          ...rows
            .slice(0, 10)
            .map((r) => ({
              id: String(r.aircraft_id),
              label: String(r.callsign ?? String(r.aircraft_id).toUpperCase()),
              altitude:
                r.on_ground === true
                  ? 'On ground'
                  : typeof r.altitude_ft === 'number'
                    ? `${number(r.altitude_ft)} ft`
                    : '—',
              speed:
                typeof r.ground_speed_kt === 'number'
                  ? `${number(r.ground_speed_kt)} kt`
                  : '—',
              heading:
                typeof r.track_deg === 'number'
                  ? `${['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.round(r.track_deg / 45) % 8]} · ${Math.round(r.track_deg)}°`
                  : '—',
              distance: `${number(distanceFromDtwNm(Number(r.latitude_deg), Number(r.longitude_deg)))} NM`,
            })),
        );
      } else if (Array.isArray(source.events))
        facts.push({
          label: 'Published airport events',
          value: `${source.events.length} events in the saved FAA feed`,
        });
    }
    for (const item of Array.isArray(root.weather) ? root.weather : []) {
      const row = object(item);
      const retained =
        typeof row.content_json === 'string'
          ? JSON.parse(row.content_json)
          : null;
      const c = retained?.[2]?.metar_evidence?.conditions;
      const scalar = retained ? JSON.parse(retained[1]) : row;
      const speed = c?.wind_speed_kt ?? scalar.windSpeedKt ?? row.wind_speed_kt;
      const direction =
        c?.wind_direction_deg ??
        scalar.windDirectionDeg ??
        row.wind_direction_deg;
      const gust = c?.wind_gust_kt ?? scalar.windGustKt ?? row.wind_gust_kt;
      const visibility =
        c?.visibility_m ?? scalar.visibilityM ?? row.visibility_m;
      const ceiling = c?.ceiling_ft ?? scalar.ceilingFt ?? row.ceiling_ft;
      if (typeof speed === 'number')
        facts.push({
          label: 'KDTW wind',
          value: `${typeof direction === 'number' ? `From ${direction}° at ` : ''}${number(speed)} knots${typeof gust === 'number' ? `, gusting ${number(gust)} knots` : ''}`,
        });
      if (typeof visibility === 'number')
        facts.push({
          label: 'Visibility',
          value: `${c?.visibility_bound === 'greater_than' ? 'More than ' : c?.visibility_bound === 'less_than' ? 'Less than ' : ''}${number(visibility / 1000)} km`,
        });
      if (typeof ceiling === 'number')
        facts.push({
          label: 'Cloud ceiling',
          value: `${number(ceiling)} feet above ground`,
        });
      else if (c?.sky_known)
        facts.push({
          label: 'Cloud ceiling',
          value: 'No ceiling reported in this observation',
        });
      const forecasts = retained?.[2]?.taf_evidence;
      if (Array.isArray(forecasts) && forecasts.length)
        facts.push({
          label: 'Forecast coverage',
          value: `${forecasts.length} saved KDTW weather forecast${forecasts.length === 1 ? '' : 's'}`,
        });
    }
  } catch {
    /* Technical source fields remain available in the expandable record. */
  }
  return { facts, planes };
}
