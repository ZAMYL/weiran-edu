import { tokens } from '../app/theme';
import { useEffect, useRef } from 'react';
import { init, use as registerCharts, type EChartsCoreOption } from 'echarts/core';
import { RadarChart, BarChart } from 'echarts/charts';
import { GridComponent, RadarComponent, TooltipComponent, AriaComponent } from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';
import type { Batch } from '../domain/model';
import { distribution, mean, scaleValues } from '../domain/statistics';

registerCharts([
  RadarChart,
  BarChart,
  GridComponent,
  RadarComponent,
  TooltipComponent,
  AriaComponent,
  SVGRenderer,
]);
export function AbilityChart({ batch, mode }: { batch: Batch; mode: 'radar' | 'distribution' }) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current) return;
    const dimensions = batch.standard.dimensions;
    const firstDimension = dimensions[0];
    if (!firstDimension) return;
    const chart = init(container.current, undefined, { renderer: 'svg' });
    const radarDimensions = [firstDimension, ...dimensions.slice(1).reverse()];
    const option: EChartsCoreOption =
      mode === 'radar'
        ? {
            aria: { enabled: true },
            tooltip: { trigger: 'item' },
            radar: {
              center: ['50%', '51%'],
              radius: container.current.clientWidth < 450 ? '48%' : '65%',
              startAngle: 90,
              splitNumber: 5,
              indicator: radarDimensions.map((d) => ({
                name: `${d.name}\n{score|${mean(batch.students, d.id)}}`,
                min: 0,
                max: d.max,
              })),
              axisName: {
                color: tokens.colorText,
                fontWeight: 600,
                fontSize: 13,
                lineHeight: 23,
                rich: {
                  score: {
                    fontSize: 18,
                    color: tokens.colorPrimary,
                    fontWeight: 700,
                    lineHeight: 25,
                  },
                },
              },
              axisLine: { lineStyle: { color: tokens.colorBorderSecondary } },
              splitLine: { lineStyle: { color: tokens.colorBorderSecondary } },
              splitArea: { areaStyle: { color: [tokens.colorBgContainer, tokens.colorFillAlter] } },
            },
            series: [
              {
                type: 'radar',
                name: '测评平均分',
                symbolSize: 8,
                lineStyle: { width: 2.5, color: tokens.colorPrimary },
                itemStyle: { color: tokens.colorPrimary },
                areaStyle: { color: tokens.colorPrimaryBgHover },
                data: [
                  {
                    value: radarDimensions.map((d) => mean(batch.students, d.id)),
                    name: `${batch.students.length} 人参评`,
                  },
                ],
              },
            ],
          }
        : {
            aria: { enabled: true },
            tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
            grid: { top: 28, right: 25, bottom: 36, left: 120 },
            xAxis: {
              type: 'value',
              minInterval: 1,
              name: '人数',
              axisLine: { show: false },
              splitLine: { lineStyle: { color: tokens.colorBorderSecondary } },
            },
            yAxis: {
              type: 'category',
              inverse: true,
              data: dimensions.map((d) => d.name),
              axisLine: { show: false },
              axisTick: { show: false },
            },
            series: scaleValues(firstDimension.min, firstDimension.max, firstDimension.step).map(
              (value, i) => ({
                name: `${value} 分`,
                type: 'bar',
                stack: 'total',
                barWidth: 24,
                itemStyle: {
                  color: [
                    tokens.colorPrimaryBg,
                    tokens.colorPrimaryBorder,
                    tokens.colorPrimaryBorderHover,
                    tokens.colorPrimaryHover,
                    tokens.colorPrimary,
                  ][i],
                },
                data: dimensions.map((d) => distribution(batch.students, d.id, [value])[0]),
              }),
            ),
          };
    chart.setOption(option);
    const observer = new ResizeObserver(() => {
      if (mode === 'radar')
        chart.setOption({
          radar: {
            radius: container.current && container.current.clientWidth < 450 ? '48%' : '65%',
          },
        });
      chart.resize();
    });
    observer.observe(container.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [batch, mode]);
  return (
    <div
      ref={container}
      className="ability-chart"
      role="img"
      aria-label={batch.standard.dimensions
        .map((d) => `${d.name}均值${mean(batch.students, d.id)}`)
        .join('，')}
    />
  );
}
