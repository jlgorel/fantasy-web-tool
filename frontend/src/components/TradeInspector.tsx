/**
 * Single-trade inspector panel.
 *
 * Rendered inline inside an expanded row of the Wrapped trade ledger.
 * Shows three things, all derived from `GET .../inspect_trade`:
 *
 *   1. The race chart -- both sides' running KTC-equivalent value vs.
 *      time. Because both sides share the same per-step active-day
 *      denominator and concavity exponent, *any visible line crossing
 *      equals a verdict flip on that exact day*. Crossover dates are
 *      drawn as vertical reference lines so the moment the trade
 *      flipped is unmistakable.
 *
 *   2. A per-side breakdown of the held assets with their average
 *      raw-KTC value (the 0-9999-scale number people recognise from
 *      KeepTradeCut).
 *
 *   3. Sparkline strips per asset so you can spot the moment that one
 *      player tanked / rocketed.
 *
 * Loading + error states render inside the same panel so the parent
 * doesn't have to coordinate -- it just toggles `transactionId` to
 * mount this component.
 *
 * Charting via recharts: hover gives a tooltip with the exact
 * KTC-equiv on each side at that date, which is the value the static
 * SVG version couldn't provide.
 */
import React, { useEffect, useMemo, useState } from 'react';
import {
  Box,
  Heading,
  HStack,
  VStack,
  Text,
  Spinner,
  SimpleGrid,
  Divider,
  Tag,
} from '@chakra-ui/react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  CartesianGrid,
  Legend,
} from 'recharts';
import { api } from '../api/client';
import {
  WrappedInspectTrade,
  WrappedPerAssetSeries,
  WrappedRaceChart,
} from '../types/player';

interface TradeInspectorProps {
  leagueId: string;
  transactionId: string;
  year: string;
}

// Side colors: kept consistent between the race chart and the
// per-asset sparkline strips so users can scan vertically. Up to 4
// sides supported (Sleeper allows N-way trades, but anything past 3
// is genuinely rare).
const SIDE_COLORS = ['#2b6cb0', '#c53030', '#2f855a', '#6b46c1'];

/** Convert ISO YYYY-MM-DD into a day-of-window x coordinate so we can
 *  use a regular numeric x-axis instead of a category one. Numeric
 *  x lets reference lines for crossover dates land precisely. */
function daysSince(iso: string, anchorIso: string): number {
  // UTC midnight for both anchors keeps DST out of the math.
  const a = Date.parse(anchorIso + 'T00:00:00Z');
  const b = Date.parse(iso + 'T00:00:00Z');
  return Math.round((b - a) / 86400000);
}

/** Pretty x-axis label for "N days since the trade." We choose
 *  monthly-ish ticks so the axis isn't crowded with raw day numbers. */
function formatDayOffset(days: number): string {
  if (days === 0) return 'trade day';
  if (days < 31) return `${days}d`;
  const months = Math.round(days / 30);
  return `${months}mo`;
}

/** Build a single recharts dataset where each row is one timestamp
 *  and each side is a separate ``y_<label>`` column. Aligned timelines
 *  guarantee equal row counts. */
function buildRaceChartData(
  race: WrappedRaceChart,
): Array<Record<string, number | string>> {
  const anchor = race.trade_date;
  const n = race.sides[0]?.points.length ?? 0;
  const rows: Array<Record<string, number | string>> = [];
  for (let i = 0; i < n; i += 1) {
    const ref = race.sides[0].points[i];
    const row: Record<string, number | string> = {
      day: daysSince(ref.date, anchor),
      date: ref.date,
    };
    for (const side of race.sides) {
      row[side.team_label] = Math.round(side.points[i].ktc_equiv);
    }
    rows.push(row);
  }
  // Trim the leading "all-sides-zero" prefix. Offseason trades have an
  // active-day count of zero until the calendar opens (~July), which
  // produces a flat-zero ramp at the start of the chart that buries
  // the meaningful curve. Drop those rows -- but always keep the
  // anchor row (day 0) so the chart is never empty for a brand-new
  // trade where every sample really is zero.
  const teamLabels = race.sides.map((s) => s.team_label);
  const firstNonZero = rows.findIndex((r) =>
    teamLabels.some((t) => Number(r[t]) > 0),
  );
  if (firstNonZero > 0) {
    return rows.slice(firstNonZero);
  }
  return rows;
}

function buildAssetSparkData(
  asset: WrappedPerAssetSeries, anchor: string,
): Array<{ day: number; date: string; value: number }> {
  return asset.points.map((p) => ({
    day: daysSince(p.date, anchor),
    date: p.date,
    value: Math.round(p.value),
  }));
}

export const TradeInspector: React.FC<TradeInspectorProps> = ({
  leagueId,
  transactionId,
  year,
}) => {
  const [data, setData] = useState<WrappedInspectTrade | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getWrappedInspectTrade(leagueId, transactionId, year)
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          // 503 from the backend means the KTC blob is unreachable; any
          // other error is the unexpected sort. We don't differentiate
          // copy here -- users mostly care "is the chart here, yes/no".
          const message =
            err instanceof Error ? err.message : 'Failed to load trade';
          setError(message);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [leagueId, transactionId, year]);

  // Pre-compute the race chart dataset; cheap but stable across renders.
  const raceData = useMemo(
    () => (data ? buildRaceChartData(data.race_chart) : []),
    [data],
  );

  if (loading) {
    return (
      <Box py={4} textAlign="center">
        <Spinner size="sm" />
        <Text fontSize="xs" color="gray.500" mt={1}>
          Loading trade history…
        </Text>
      </Box>
    );
  }

  if (error || !data) {
    return (
      <Box py={3}>
        <Text fontSize="xs" color="red.600">
          Couldn't load value history for this trade.
          {error ? ` (${error})` : ''}
        </Text>
      </Box>
    );
  }

  const anchor = data.race_chart.trade_date;
  // Side index per team_label so the per-asset sparkline gets the
  // same color as that side's race-chart line.
  const sideIndexByLabel: Record<string, number> = {};
  data.race_chart.sides.forEach((s, i) => {
    sideIndexByLabel[s.team_label] = i;
  });

  // Group per-asset rows by side so the breakdown reads as columns.
  const assetsBySide: Record<string, WrappedPerAssetSeries[]> = {};
  data.per_asset_series.forEach((row) => {
    (assetsBySide[row.team_label] ||= []).push(row);
  });

  // Crossover x-positions -- vertical ReferenceLines on the race chart.
  const crossoverDays = data.race_chart.crossover_dates.map((iso) =>
    daysSince(iso, anchor),
  );

  // Pick-resolution markers: each pick that became a specific player
  // gets a labelled vertical line at the draft date on both the race
  // chart and the matching per-asset sparkline.
  const pickResolutions =
    data.race_chart.pick_resolutions ?? data.pick_resolutions ?? [];
  const pickResolutionDays = pickResolutions.map((r) => ({
    ...r,
    day: daysSince(r.date, anchor),
    // Render "2024 R2 → Jalen Milroe" when the backend supplies a pick
    // descriptor; otherwise fall back to just the player name.
    display: r.pick_label ? `${r.pick_label} → ${r.label}` : r.label,
  }));
  const pickResolutionsByAsset: Record<
    string,
    { date: string; label: string; display: string; day: number }
  > = {};
  pickResolutionDays.forEach((r) => {
    pickResolutionsByAsset[r.asset_id] = {
      date: r.date,
      label: r.label,
      display: r.display,
      day: r.day,
    };
  });

  return (
    <Box>
      {/* Race chart: running KTC-equivalent value per side */}
      <Box>
        <Heading size="xs" mb={1}>
          Cumulative value held since trade
        </Heading>
        <Text fontSize="2xs" color="gray.500" mb={2}>
          Higher line = that side held more KTC value over the lookback
          window. Crossings = the verdict actually flipped on that date.
        </Text>
        <Box height="220px">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={raceData}
              margin={{ top: 4, right: 16, left: 0, bottom: 4 }}
            >
              <CartesianGrid stroke="#edf2f7" strokeDasharray="3 3" />
              <XAxis
                dataKey="day"
                type="number"
                domain={['dataMin', 'dataMax']}
                tickFormatter={formatDayOffset}
                tick={{ fontSize: 10 }}
              />
              <YAxis
                tick={{ fontSize: 10 }}
                width={48}
                label={{
                  value: 'KTC equiv',
                  angle: -90,
                  position: 'insideLeft',
                  style: { fontSize: 10, fill: '#718096' },
                }}
              />
              <Tooltip
                labelFormatter={(day) => {
                  const row = raceData.find((r) => r.day === day);
                  return row ? `${row.date} (day ${day})` : `day ${day}`;
                }}
                formatter={(v) => Number(v).toLocaleString()}
                contentStyle={{ fontSize: 11 }}
              />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {crossoverDays.map((d) => (
                <ReferenceLine
                  key={d}
                  x={d}
                  stroke="#a0aec0"
                  strokeDasharray="4 2"
                  label={{
                    value: 'flip',
                    fontSize: 9,
                    fill: '#718096',
                    position: 'top',
                  }}
                />
              ))}
              {pickResolutionDays.map((r) => (
                <ReferenceLine
                  key={`pr-${r.asset_id}`}
                  x={r.day}
                  stroke="#805ad5"
                  strokeDasharray="2 3"
                  ifOverflow="extendDomain"
                  label={{
                    value: r.display,
                    fontSize: 9,
                    fill: '#553c9a',
                    position: 'insideBottom',
                    offset: 12,
                  }}
                />
              ))}
              {data.race_chart.sides.map((side, i) => (
                <Line
                  key={side.team_label}
                  type="monotone"
                  dataKey={side.team_label}
                  stroke={SIDE_COLORS[i % SIDE_COLORS.length]}
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </Box>
        {data.race_chart.crossover_dates.length > 0 && (
          <Text fontSize="2xs" color="gray.600" mt={1}>
            Verdict flipped on:{' '}
            {data.race_chart.crossover_dates.join(', ')}
          </Text>
        )}
      </Box>

      <Divider my={3} />

      {/* Per-side breakdown */}
      <SimpleGrid columns={{ base: 1, md: 2 }} gap={3}>
        {data.trade.sides.map((side, i) => (
          <Box key={side.username}>
            <HStack mb={1} justify="space-between">
              <Text
                fontWeight={
                  side.username === data.trade.winner ? 'semibold' : 'normal'
                }
                fontSize="sm"
              >
                {side.username}
                {side.username === data.trade.winner && (
                  <Tag size="sm" colorScheme="green" ml={2}>
                    winner
                  </Tag>
                )}
              </Text>
              <Text fontSize="xs" color="gray.600">
                {side.ktc_equiv.toFixed(0)} KTC equiv
              </Text>
            </HStack>
            <VStack align="stretch" gap={2}>
              {(assetsBySide[side.username] || []).map((asset) => {
                const idx = sideIndexByLabel[side.username] ?? i;
                const color = SIDE_COLORS[idx % SIDE_COLORS.length];
                const sparkData = buildAssetSparkData(asset, anchor);
                const avg =
                  side.assets.find((a) => a.asset_id === asset.asset_id)
                    ?.avg_ktc ?? 0;
                const resolution = pickResolutionsByAsset[asset.asset_id];
                return (
                  <Box
                    key={asset.asset_id}
                    borderWidth={1}
                    borderRadius="sm"
                    p={2}
                  >
                    <HStack justify="space-between" mb={1}>
                      <Text fontSize="xs" fontWeight="medium">
                        {asset.label}
                      </Text>
                      <Text fontSize="2xs" color="gray.500">
                        avg {avg.toFixed(0)}
                      </Text>
                    </HStack>
                    <Box height="60px">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart
                          data={sparkData}
                          margin={{ top: 2, right: 4, left: 0, bottom: 2 }}
                        >
                          <XAxis
                            dataKey="day"
                            type="number"
                            domain={['dataMin', 'dataMax']}
                            hide
                          />
                          <YAxis hide domain={['auto', 'auto']} />
                          <Tooltip
                            labelFormatter={(day) => {
                              const row = sparkData.find((r) => r.day === day);
                              return row ? row.date : `day ${day}`;
                            }}
                            formatter={(v) => Number(v).toLocaleString()}
                            contentStyle={{ fontSize: 10, padding: 4 }}
                          />
                          {resolution && (
                            <ReferenceLine
                              x={resolution.day}
                              stroke="#805ad5"
                              strokeDasharray="2 3"
                              ifOverflow="extendDomain"
                              label={{
                                value: resolution.display,
                                fontSize: 8,
                                fill: '#553c9a',
                                position: 'insideBottom',
                                offset: 2,
                              }}
                            />
                          )}
                          <Line
                            type="monotone"
                            dataKey="value"
                            stroke={color}
                            strokeWidth={1.5}
                            dot={false}
                            isAnimationActive={false}
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    </Box>
                    {resolution && (
                      <Text fontSize="2xs" color="purple.600" mt={1}>
                        {resolution.date} — {resolution.display}
                      </Text>
                    )}
                  </Box>
                );
              })}
            </VStack>
          </Box>
        ))}
      </SimpleGrid>
    </Box>
  );
};
