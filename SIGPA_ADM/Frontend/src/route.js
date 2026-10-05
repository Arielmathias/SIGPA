export function moveStop(stops, index, delta) {
  const next = index + delta;
  if (index < 0 || index >= stops.length || next < 0 || next >= stops.length) return stops;
  const result = [...stops];
  [result[index], result[next]] = [result[next], result[index]];
  return result;
}
