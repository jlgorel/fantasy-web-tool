/**
 * Tiny dependency-free SVG charts for the Player Detail + Wrapped pages.
 *
 * We deliberately avoid pulling in recharts / nivo — both pages render only
 * a handful of data points and we already have Chakra for layout, so a
 * ~150-line SVG helper keeps the bundle lean and renders crisply on mobile.
 */
import React from 'react';
import { Box, Text } from '@chakra-ui/react';

export interface LineSeries {
  label: string;
  color: string;
  points: { x: number; y: number }[];
}

interface LineChartProps {
  series: LineSeries[];
  xLabel?: string;
  yLabel?: string;
  height?: number;
  // Force the y-axis floor (e.g. 0 for ownership %). When omitted we use the
  // data's own min so small fluctuations are visually meaningful.
  yMin?: number;
  yMax?: number;
  // Renders a faint horizontal reference line (e.g. league median).
  refLine?: { y: number; label?: string };
}

export const LineChart: React.FC<LineChartProps> = ({
  series,
  xLabel,
  yLabel,
  height = 220,
  yMin,
  yMax,
  refLine,
}) => {
  const padding = { top: 12, right: 16, bottom: 28, left: 40 };
  const width = 560; // SVG viewBox; scales fluidly via CSS
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;

  const allPoints = series.flatMap((s) => s.points);
  if (allPoints.length === 0) {
    return (
      <Box py={4} textAlign="center" color="gray.500">
        <Text fontSize="sm">No data to chart.</Text>
      </Box>
    );
  }

  const xs = allPoints.map((p) => p.x);
  const ys = allPoints.map((p) => p.y);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const minY = yMin != null ? yMin : Math.min(...ys);
  const maxY = yMax != null ? yMax : Math.max(...ys);
  const yRange = maxY - minY || 1;
  const xRange = xMax - xMin || 1;

  const projectX = (x: number) => padding.left + ((x - xMin) / xRange) * innerW;
  const projectY = (y: number) =>
    padding.top + innerH - ((y - minY) / yRange) * innerH;

  // Five horizontal gridlines at evenly-spaced y-values.
  const gridYs = Array.from({ length: 5 }, (_, i) => minY + (yRange * i) / 4);

  return (
    <Box width="100%" overflowX="auto">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="xMidYMid meet"
        style={{ width: '100%', height: 'auto' }}
        role="img"
        aria-label={`${yLabel || ''} line chart`}
      >
        {/* Gridlines + y-axis labels */}
        {gridYs.map((gy, i) => (
          <g key={i}>
            <line
              x1={padding.left}
              x2={width - padding.right}
              y1={projectY(gy)}
              y2={projectY(gy)}
              stroke="#e2e8f0"
              strokeWidth={1}
            />
            <text
              x={padding.left - 6}
              y={projectY(gy) + 4}
              textAnchor="end"
              fontSize={10}
              fill="#4a5568"
            >
              {Number.isInteger(gy) ? gy.toFixed(0) : gy.toFixed(1)}
            </text>
          </g>
        ))}

        {/* Reference line */}
        {refLine && (
          <g>
            <line
              x1={padding.left}
              x2={width - padding.right}
              y1={projectY(refLine.y)}
              y2={projectY(refLine.y)}
              stroke="#a0aec0"
              strokeDasharray="4 3"
              strokeWidth={1}
            />
            {refLine.label && (
              <text
                x={width - padding.right - 4}
                y={projectY(refLine.y) - 4}
                textAnchor="end"
                fontSize={10}
                fill="#4a5568"
              >
                {refLine.label}
              </text>
            )}
          </g>
        )}

        {/* x-axis tick labels (just the integer xs we have) */}
        {Array.from(new Set(xs)).map((x) => (
          <text
            key={x}
            x={projectX(x)}
            y={height - padding.bottom + 14}
            textAnchor="middle"
            fontSize={10}
            fill="#4a5568"
          >
            {x}
          </text>
        ))}

        {/* Series lines + points */}
        {series.map((s) => {
          const path = s.points
            .map(
              (p, i) => `${i === 0 ? 'M' : 'L'} ${projectX(p.x)} ${projectY(p.y)}`,
            )
            .join(' ');
          return (
            <g key={s.label}>
              <path d={path} fill="none" stroke={s.color} strokeWidth={2} />
              {s.points.map((p, i) => (
                <circle
                  key={i}
                  cx={projectX(p.x)}
                  cy={projectY(p.y)}
                  r={3}
                  fill={s.color}
                />
              ))}
            </g>
          );
        })}

        {/* Axis labels */}
        {xLabel && (
          <text
            x={padding.left + innerW / 2}
            y={height - 4}
            textAnchor="middle"
            fontSize={11}
            fill="#2d3748"
          >
            {xLabel}
          </text>
        )}
        {yLabel && (
          <text
            x={12}
            y={padding.top + innerH / 2}
            textAnchor="middle"
            fontSize={11}
            fill="#2d3748"
            transform={`rotate(-90, 12, ${padding.top + innerH / 2})`}
          >
            {yLabel}
          </text>
        )}
      </svg>

      {series.length > 1 && (
        <Box display="flex" gap={3} flexWrap="wrap" mt={1}>
          {series.map((s) => (
            <Box key={s.label} display="flex" alignItems="center" gap={1}>
              <Box w="10px" h="10px" bg={s.color} borderRadius="full" />
              <Text fontSize="xs" color="gray.700">
                {s.label}
              </Text>
            </Box>
          ))}
        </Box>
      )}
    </Box>
  );
};
