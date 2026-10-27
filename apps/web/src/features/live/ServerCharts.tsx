'use client';
import { useEffect, useRef } from 'react';
import { init, use as register, type EChartsCoreOption } from 'echarts/core';
import { RadarChart, BarChart, ScatterChart } from 'echarts/charts';
import {
  AriaComponent,
  GridComponent,
  RadarComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';
import {
  DIMENSIONS,
  type ApiCondition,
  type ApiRubric,
  type ApiStatistics,
} from '../../services/edu-api/types';
import { dimensionName, scoreDisplay } from '../../services/edu-api/view-model';

register([
  RadarChart,
  BarChart,
  ScatterChart,
  GridComponent,
  RadarComponent,
  TooltipComponent,
  VisualMapComponent,
  AriaComponent,
  SVGRenderer,
]);
export function ServerAbilityChart({
  statistics,
  rubric,
  mode,
}: {
  statistics: ApiStatistics;
  rubric: ApiRubric;
  mode: 'radar' | 'distribution';
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const chart = init(element, undefined, { renderer: 'svg' });
    const ordered = [DIMENSIONS[0], ...DIMENSIONS.slice(1).reverse()]
      .map((d) => statistics.dimensions.find((s) => s.dimension_code === d.code))
      .filter((s) => s !== undefined);
    const option: EChartsCoreOption =
      mode === 'radar'
        ? {
            aria: {
              enabled: true,
              label: {
                description: statistics.dimensions
                  .map((d) => `${dimensionName(d.dimension_code)}均值${scoreDisplay(d.mean.value)}`)
                  .join('，'),
              },
            },
            tooltip: { trigger: 'item' },
            radar: {
              center: ['50%', '53%'],
              radius: element.clientWidth < 450 ? '46%' : '64%',
              indicator: ordered.map((d) => ({
                name: `${dimensionName(d.dimension_code)}\n${scoreDisplay(d.mean.value)}`,
                min: 0,
                max: rubric.score_policy.maximum,
              })),
              axisName: { color: '#274570', fontSize: 12, lineHeight: 22 },
              axisLine: { lineStyle: { color: '#dce4f1' } },
              splitLine: { lineStyle: { color: '#dce4f1' } },
              splitArea: { areaStyle: { color: ['#fff', '#fbfcff'] } },
            },
            series: [
              {
                type: 'radar',
                symbolSize: 7,
                itemStyle: { color: '#2463f5' },
                lineStyle: { width: 2.5 },
                areaStyle: { color: 'rgba(36,99,245,.22)' },
                data: [{ value: ordered.map((d) => d.mean.value), name: '测评平均分' }],
              },
            ],
          }
        : {
            aria: {
              enabled: true,
              label: {
                description: statistics.dimensions
                  .map((d) => `${dimensionName(d.dimension_code)}均值${scoreDisplay(d.mean.value)}`)
                  .join('，'),
              },
            },
            tooltip: { trigger: 'axis' },
            grid: { left: 45, right: 25, top: 20, bottom: 65 },
            xAxis: {
              type: 'value',
              min: rubric.score_policy.minimum,
              max: rubric.score_policy.maximum,
              name: '原始评分',
              nameLocation: 'middle',
              nameGap: 32,
            },
            yAxis: {
              type: 'category',
              data: DIMENSIONS.map((d) => d.name),
              axisLabel: { show: false },
            },
            visualMap: {
              min: 0,
              max: Math.max(
                1,
                ...statistics.dimensions.flatMap((d) => d.distribution.map((f) => f.count)),
              ),
              dimension: 2,
              orient: 'horizontal',
              bottom: 0,
              left: 'center',
              inRange: { color: ['#dceaff', '#2463f5'] },
              text: ['人数多', '少'],
            },
            series: [
              {
                type: 'scatter',
                symbol: 'rect',
                symbolSize: 17,
                data: statistics.dimensions.flatMap((d) =>
                  d.distribution.map((f) => [
                    f.score,
                    DIMENSIONS.findIndex((item) => item.code === d.dimension_code),
                    f.count,
                  ]),
                ),
                tooltip: {
                  formatter: (params: unknown) => {
                    const p = params as { value: number[] };
                    return `${DIMENSIONS[p.value[1] ?? -1]?.name ?? ''}：${p.value[0]} 分，参评人数 ${p.value[2]} 人`;
                  },
                },
              },
            ],
          };
    chart.setOption(option);
    const observer = new ResizeObserver(() => {
      if (mode === 'radar')
        chart.setOption({ radar: { radius: element.clientWidth < 450 ? '46%' : '64%' } });
      chart.resize();
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [statistics, rubric, mode]);
  return (
    <div
      className="ability-chart server-chart"
      ref={ref}
      role="img"
      aria-label={statistics.dimensions
        .map((d) => `${dimensionName(d.dimension_code)}均值${scoreDisplay(d.mean.value)}`)
        .join('，')}
    />
  );
}
export function selectedByAxes(
  x: number,
  y: number,
  statistics: ApiStatistics,
  conditions: ApiCondition[],
) {
  const matrix = statistics.matrix;
  if (
    !matrix ||
    !conditions.length ||
    conditions.some(
      (c) => c.dimension_code !== matrix.x_dimension && c.dimension_code !== matrix.y_dimension,
    )
  )
    return false;
  return conditions.every((c) => {
    const value = c.dimension_code === matrix.x_dimension ? x : y;
    switch (c.operator) {
      case 'lt':
        return value < c.value;
      case 'lte':
        return value <= c.value;
      case 'eq':
        return value === c.value;
      case 'gte':
        return value >= c.value;
      case 'gt':
        return value > c.value;
    }
  });
}
export function SparseMatrix({
  statistics,
  conditions,
  onCell,
}: {
  statistics: ApiStatistics;
  conditions: ApiCondition[];
  onCell: (x: number, y: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = ref.current,
      matrix = statistics.matrix;
    if (!element || !matrix) return;
    const chart = init(element, undefined, { renderer: 'svg' });
    const max = Math.max(1, ...matrix.cells.map((c) => c.count));
    chart.setOption({
      aria: {
        enabled: true,
        label: {
          description: `实际评分组合，${matrix.cells.length} 个实际评分组合，配对人数 ${matrix.paired_count}`,
        },
      },
      tooltip: {
        trigger: 'item',
        formatter: (p: { value: number[] }) =>
          `${dimensionName(matrix.x_dimension)} ${p.value[0]} 分<br/>${dimensionName(matrix.y_dimension)} ${p.value[1]} 分<br/>参评人数 ${p.value[2]} 人`,
      },
      grid: { left: 50, right: 24, top: 30, bottom: 95 },
      xAxis: {
        type: 'value',
        min: 1,
        max: 5,
        interval: 1,
        name: dimensionName(matrix.x_dimension),
        nameLocation: 'middle',
        nameGap: 32,
        axisLabel: {
          formatter: (v: number) => (Number.isInteger(v) && v >= 1 && v <= 5 ? `${v}` : ''),
        },
      },
      yAxis: {
        type: 'value',
        min: 1,
        max: 5,
        interval: 1,
        name: dimensionName(matrix.y_dimension),
        nameLocation: 'end',
        nameTextStyle: { fontSize: 11 },
        axisLabel: {
          formatter: (v: number) => (Number.isInteger(v) && v >= 1 && v <= 5 ? `${v}` : ''),
        },
      },
      visualMap: {
        min: 0,
        max,
        dimension: 2,
        orient: 'horizontal',
        left: 'center',
        bottom: 8,
        inRange: { color: ['#bed4ff', '#2463f5'] },
        text: ['人数多', '少'],
      },
      series: [
        {
          type: 'scatter',
          clip: false,
          symbol: 'rect',
          symbolSize: (value: number[]) => 12 + 16 * Math.sqrt((value[2] ?? 0) / max),
          data: matrix.cells.map((cell) => ({
            value: [cell.x, cell.y, cell.count],
            itemStyle: {
              borderWidth: selectedByAxes(cell.x, cell.y, statistics, conditions) ? 2 : 0,
              borderColor: '#163fe3',
            },
          })),
        },
      ],
    });
    chart.on('click', (params) => {
      if (Array.isArray(params.value)) {
        const [x, y] = params.value;
        if (typeof x === 'number' && typeof y === 'number') onCell(x, y);
      }
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(element);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [statistics, conditions, onCell]);
  return (
    <>
      <div
        ref={ref}
        className="sparse-matrix"
        role="img"
        aria-label={`实际评分组合，${statistics.matrix?.cells.length ?? 0} 个实际评分组合，配对人数 ${statistics.matrix?.paired_count ?? 0}`}
      />
      <p className="muted small">
        每个色块为实际评分组合，保留小数；不扩展为完整网格。点击色块精确筛选。
      </p>
    </>
  );
}
