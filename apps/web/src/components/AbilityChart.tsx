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
                color: '#253454',
                fontWeight: 600,
                fontSize: 13,
                lineHeight: 23,
                rich: {
                  score: { fontSize: 18, color: '#2463f5', fontWeight: 700, lineHeight: 25 },
                },
              },
              axisLine: { lineStyle: { color: '#dce4f1' } },
              splitLine: { lineStyle: { color: '#dce4f1' } },
              splitArea: { areaStyle: { color: ['#ffffff', '#fbfcff'] } },
            },
            series: [
              {
                type: 'radar',
                name: '测评平均分',
                symbolSize: 8,
                lineStyle: { width: 2.5, color: '#2463f5' },
                itemStyle: { color: '#2463f5' },
                areaStyle: { color: 'rgba(36,99,245,.23)' },
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
              splitLine: { lineStyle: { color: '#edf1f8' } },
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
                itemStyle: { color: ['#e6efff', '#bcd5ff', '#87b3ff', '#4e8cff', '#2463f5'][i] },
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
