import {
  checked,
  count,
  list,
  literal,
  nullable,
  numeric,
  object,
  optional,
  text,
  type Decoded,
} from './schema';
import { exactRationalDto } from './exact';

export const statValueDto = object({
  value: nullable(numeric),
  exact: nullable(exactRationalDto),
  display: text,
});

const nonBlank = checked(text, (value) => value.trim().length > 0);
const decimal = checked(
  text,
  (value) =>
    value.length <= 128 && /^-?\d+(?:\.\d+)?$/.test(value) && Number.isFinite(Number(value)),
);
const scaleNumber = (value: unknown) =>
  typeof value === 'string' ? decimal(value) : numeric(value);
export const itemScaleDto = checked(
  object({
    minimum: scaleNumber,
    maximum: scaleNumber,
    step: scaleNumber,
    unit: optional(nonBlank),
  }),
  ({ minimum, maximum, step }) => Number(minimum) <= Number(maximum) && Number(step) > 0,
);
const itemShape = {
  item_id: nonBlank,
  display_name: nonBlank,
  kind: literal('competency', 'question'),
  scale: nullable(itemScaleDto),
};
const uniqueItems = <T extends { item_id: string }>(items: T[]) =>
  new Set(items.map((item) => item.item_id)).size === items.length;
const itemDto = object(itemShape);
export const assessmentItemDto = itemDto;
export type AssessmentItem = Decoded<typeof itemDto>;
export const previewItemsDto = checked(
  list(object({ ...itemShape, score_row_count: count })),
  uniqueItems,
);
export const batchItemsDto = checked(
  object({
    recognized_items: optional(checked(list(itemDto), uniqueItems)),
    available_analyses: optional(list(nonBlank)),
  }),
  ({ recognized_items, available_analyses }) =>
    (recognized_items === undefined) === (available_analyses === undefined),
);

export function itemScaleLabel(scale: AssessmentItem['scale']) {
  return scale ? `${scale.minimum}–${scale.maximum} ${scale.unit ?? '分'}` : '量表未提供';
}
