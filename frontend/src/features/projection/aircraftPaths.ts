import type {
  AircraftPathPoint,
  AircraftTrajectory,
  Projection,
} from '../live/types';

export interface MapProjection {
  projection: Projection | undefined;
  expectedSnapshotId: string;
  minute: number;
  mode: 'LIVE' | 'TEST';
}
function validSeries(points: readonly AircraftPathPoint[]) {
  return (
    points.length >= 2 &&
    points.every(
      (point, index) =>
        Number.isFinite(point.minute) &&
        point.minute >= 0 &&
        point.minute <= 15 &&
        Number.isFinite(point.latitudeDeg) &&
        Math.abs(point.latitudeDeg) <= 90 &&
        Number.isFinite(point.longitudeDeg) &&
        Math.abs(point.longitudeDeg) <= 180 &&
        (index === 0 || point.minute > points[index - 1].minute),
    )
  );
}
export function supportedPaths(
  update: MapProjection | null,
): readonly AircraftTrajectory[] {
  const projection = update?.projection;
  const paths = projection?.aircraftPaths;
  if (
    !update ||
    !projection ||
    projection.snapshotId !== update.expectedSnapshotId ||
    !paths ||
    (paths.basis !== 'modeled' && paths.basis !== 'illustrative') ||
    (paths.basis === 'illustrative' && update.mode !== 'TEST')
  )
    return [];
  const ids = new Set<string>();
  return paths.trajectories.filter((trajectory) => {
    if (
      !trajectory.aircraftId ||
      ids.has(trajectory.aircraftId) ||
      !validSeries(trajectory.baseline) ||
      !validSeries(trajectory.proposed)
    )
      return false;
    ids.add(trajectory.aircraftId);
    return true;
  });
}
// Interpolate only between supplied model waypoints; never extrapolate live tracks.
export function projectedPosition(
  points: readonly AircraftPathPoint[],
  minute: number,
) {
  if (
    !Number.isFinite(minute) ||
    !validSeries(points) ||
    minute < points[0].minute ||
    minute > points[points.length - 1].minute
  )
    return null;
  const end = points.findIndex((point) => point.minute >= minute);
  const start = Math.max(0, end - 1);
  const a = points[start];
  const b = points[end];
  const fraction = a === b ? 0 : (minute - a.minute) / (b.minute - a.minute);
  const headingEnd = a === b ? points[Math.min(end + 1, points.length - 1)] : b;
  const heading =
    ((Math.atan2(
      (headingEnd.longitudeDeg - a.longitudeDeg) *
        Math.cos((a.latitudeDeg * Math.PI) / 180),
      headingEnd.latitudeDeg - a.latitudeDeg,
    ) *
      180) /
      Math.PI +
      360) %
    360;
  return {
    longitudeDeg: a.longitudeDeg + (b.longitudeDeg - a.longitudeDeg) * fraction,
    latitudeDeg: a.latitudeDeg + (b.latitudeDeg - a.latitudeDeg) * fraction,
    headingDeg: heading,
  };
}
export function pathFeatures(update: MapProjection | null) {
  return {
    type: 'FeatureCollection' as const,
    features: supportedPaths(update).flatMap((trajectory) =>
      (['baseline', 'proposed'] as const).map((branch) => ({
        type: 'Feature' as const,
        properties: { aircraftId: trajectory.aircraftId, branch },
        geometry: {
          type: 'LineString' as const,
          coordinates: trajectory[branch].map((p) => [
            p.longitudeDeg,
            p.latitudeDeg,
          ]),
        },
      })),
    ),
  };
}
