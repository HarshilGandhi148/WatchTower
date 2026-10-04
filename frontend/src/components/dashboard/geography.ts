import type { Aircraft } from '../../features/live/types';

// FAA KDTW airport reference point: 42-12-44.752N / 83-21-12.216W.
// https://www.faa.gov/air_traffic/publications/atpubs/aip_html/part3_ad_2.0_michigan.html
export const DTW_CENTER = [-83.35339333333333, 42.21243111111111] as const;
export const MIN_VIEW_RADIUS_NM = 5;
export const MAX_VIEW_RADIUS_NM = 40;
const EARTH_RADIUS_NM = 3440.065;
const radians = (degrees: number) => (degrees * Math.PI) / 180;
export function distanceFromDtwNm(latitudeDeg: number, longitudeDeg: number) {
  const latitude = radians(latitudeDeg);
  const centerLatitude = radians(DTW_CENTER[1]);
  const h =
    Math.sin((latitude - centerLatitude) / 2) ** 2 +
    Math.cos(centerLatitude) *
      Math.cos(latitude) *
      Math.sin(radians(longitudeDeg - DTW_CENTER[0]) / 2) ** 2;
  return (
    2 * EARTH_RADIUS_NM * Math.asin(Math.sqrt(Math.min(1, Math.max(0, h))))
  );
}
export function currentAircraft(aircraft: readonly Aircraft[], now: number) {
  return aircraft.filter((a) => {
    const age = now - Date.parse(a.observedAtUtc);
    return (
      Number.isFinite(a.latitudeDeg) &&
      Math.abs(a.latitudeDeg) <= 90 &&
      Number.isFinite(a.longitudeDeg) &&
      Math.abs(a.longitudeDeg) <= 180 &&
      Number.isFinite(age) &&
      age >= 0 &&
      age <= 120_000 &&
      distanceFromDtwNm(a.latitudeDeg, a.longitudeDeg) <= 40
    );
  });
}
export function rangeRing(radiusNm: number) {
  const latitude = radians(DTW_CENTER[1]);
  const longitude = radians(DTW_CENTER[0]);
  const distance = radiusNm / EARTH_RADIUS_NM;
  return Array.from({ length: 97 }, (_, index) => {
    const bearing = (index / 96) * Math.PI * 2;
    const lat = Math.asin(
      Math.sin(latitude) * Math.cos(distance) +
        Math.cos(latitude) * Math.sin(distance) * Math.cos(bearing),
    );
    const lon =
      longitude +
      Math.atan2(
        Math.sin(bearing) * Math.sin(distance) * Math.cos(latitude),
        Math.cos(distance) - Math.sin(latitude) * Math.sin(lat),
      );
    return [(lon * 180) / Math.PI, (lat * 180) / Math.PI];
  });
}
