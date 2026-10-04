export function requireRole(
  actual: string | undefined,
  required: string,
): void {
  if (actual !== required) throw new Error('Unauthorized');
}
