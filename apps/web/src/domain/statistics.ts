import type { Batch, Condition, DimensionId, Student } from './model';

export function matches(student: Student, conditions: Condition[]): boolean {
  return conditions.every(({ dimension, operator, value }) => {
    const score = student.ratings[dimension].value;
    switch (operator) {
      case '>=':
        return score >= value;
      case '<':
        return score < value;
      case '=':
        return score === value;
      case '<=':
        return score <= value;
      case '>':
        return score > value;
    }
  });
}
export function filterStudents(students: Student[], conditions: Condition[]) {
  return students.filter((student) => matches(student, conditions));
}
export function percentage(count: number, total: number) {
  return total ? `${Number(((count / total) * 100).toFixed(1))}%` : '0%';
}
export function mean(students: Student[], dimension: DimensionId) {
  return students.length
    ? Number(
        (
          students.reduce((sum, s) => sum + s.ratings[dimension].value, 0) / students.length
        ).toFixed(1),
      )
    : 0;
}
export function distribution(students: Student[], dimension: DimensionId, values: number[]) {
  return values.map((value) => students.filter((s) => s.ratings[dimension].value === value).length);
}
export function scaleValues(min: number, max: number, step: number) {
  return Array.from({ length: Math.floor((max - min) / step) + 1 }, (_, i) => min + i * step);
}
export function heatmap(batch: Batch, x: DimensionId, y: DimensionId) {
  const xd = batch.standard.dimensions.find((d) => d.id === x)!;
  const yd = batch.standard.dimensions.find((d) => d.id === y)!;
  const xs = scaleValues(xd.min, xd.max, xd.step);
  const ys = scaleValues(yd.min, yd.max, yd.step).reverse();
  return {
    xs,
    ys,
    cells: ys.map((yv) =>
      xs.map(
        (xv) =>
          batch.students.filter((s) => s.ratings[x].value === xv && s.ratings[y].value === yv)
            .length,
      ),
    ),
  };
}
export function conditionLabel(batch: Batch, condition: Condition) {
  return `${batch.standard.dimensions.find((d) => d.id === condition.dimension)?.name} ${condition.operator.replace('>=', '≥').replace('<=', '≤')} ${condition.value}`;
}
