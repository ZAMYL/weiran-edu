'use client';
import { useEffect, useRef } from 'react';
import { init, use as register, type EChartsCoreOption } from 'echarts/core';
import { BarChart, ScatterChart } from 'echarts/charts';
import {
  AriaComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';
import type {
  DiscoveryChart as ChartData,
  DiscoveryExact,
} from '../../services/edu-api/discovery-types';
import { dimensionName, scoreDisplay } from '../../services/edu-api/view-model';

register([
  BarChart,
  ScatterChart,
  AriaComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  SVGRenderer,
]);
export const exactValue = (value: DiscoveryExact) => value.numerator / value.denominator;
export const scoreText = (value: DiscoveryExact | null) =>
  value === null ? '—' : scoreDisplay(exactValue(value));

export function DiscoveryChart({ data }: { data: ChartData }) {
  const element = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!element.current) return;
    const chart = init(element.current, undefined, { renderer: 'svg' });
    const common = {
      aria: { enabled: true },
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v: unknown) => (typeof v === 'number' ? scoreDisplay(v) : String(v)),
      },
      grid: { left: 55, right: 30, top: 45, bottom: 85 },
      legend: { top: 0 },
    };
    let option: EChartsCoreOption;
    switch (data.type) {
      case 'dimension_relation':
        option = {
          ...common,
          tooltip: { trigger: 'item' },
          xAxis: {
            type: 'value',
            min: 1,
            max: 5,
            name: dimensionName(data.metrics.left),
            nameLocation: 'middle',
            nameGap: 35,
          },
          yAxis: { type: 'value', min: 1, max: 5, name: dimensionName(data.metrics.right) },
          series: [
            {
              type: 'scatter',
              name: '评分组合',
              data: data.chart.map((p) => [exactValue(p.x), exactValue(p.y), p.count]),
              symbolSize: (v: number[]) => Math.min(38, 8 + Math.sqrt(v[2] ?? 0) * 3),
              tooltip: {
                formatter: (p: unknown) => {
                  const v = (p as { value: number[] }).value;
                  return `${dimensionName(data.metrics.left)} ${scoreDisplay(v[0] ?? null)} 分<br/>${dimensionName(data.metrics.right)} ${scoreDisplay(v[1] ?? null)} 分<br/>${v[2]} 人`;
                },
              },
            },
          ],
        };
        break;
      case 'group_difference':
        option = {
          ...common,
          xAxis: {
            type: 'category',
            data: data.chart.map((p) => dimensionName(p.dimension_code)),
            axisLabel: { rotate: 20 },
          },
          yAxis: { type: 'value', min: 0, max: 5, name: '均值' },
          series: [
            {
              type: 'bar',
              name: '选定群体',
              data: data.chart.map((p) =>
                p.target.mean === null ? null : exactValue(p.target.mean),
              ),
            },
            {
              type: 'bar',
              name: '其余学生',
              data: data.chart.map((p) =>
                p.remainder.mean === null ? null : exactValue(p.remainder.mean),
              ),
            },
          ],
        };
        break;
      case 'rare_combination':
        option = {
          ...common,
          tooltip: { trigger: 'axis', valueFormatter: (v: unknown) => `${v} 人` },
          xAxis: {
            type: 'category',
            name: '两维评分差值',
            nameLocation: 'middle',
            nameGap: 40,
            data: data.chart.map((p) => scoreText(p.difference)),
            axisLabel: { rotate: 30 },
          },
          yAxis: { type: 'value', minInterval: 1, name: '人数' },
          series: [{ type: 'bar', name: '完整配对范围', data: data.chart.map((p) => p.count) }],
        };
        break;
      case 'boundary_sensitivity':
        option = {
          ...common,
          tooltip: { trigger: 'axis', valueFormatter: (v: unknown) => `${v} 人` },
          xAxis: {
            type: 'category',
            data: data.chart.map((p) => (p.operator === 'lt' ? '低于边界' : '包含边界')),
          },
          yAxis: { type: 'value', minInterval: 1, name: '人数' },
          series: [{ type: 'bar', name: '符合条件的学生', data: data.chart.map((p) => p.count) }],
        };
    }
    chart.setOption(option);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(element.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [data]);
  return (
    <div
      ref={element}
      style={{ height: 350, width: '100%' }}
      role="img"
      aria-label="发现完整统计图表"
      data-testid="discovery-chart"
    />
  );
}
