import React, { useMemo } from 'react';
import {
  Box,
  HStack,
  VStack,
  Text,
  Tooltip,
  Stat,
  StatLabel,
  StatNumber,
  StatHelpText,
} from '@chakra-ui/react';
import { Player } from '../types/player';

interface LineupConfidenceProps {
  starters: Player[];
}

interface SimResult {
  mean: number;
  p10: number;
  p90: number;
  pctileCovered: number; // fraction of starter slots that had real percentile data
  totalSlots: number;
  deterministicFloor: number; // points contributed by DEF/K/Vegas-only players
}

const N_SIMS = 4000;

function parseVegas(v: string | undefined): number | null {
  if (!v) return null;
  // VEGAS strings can be "12.34" or "12.34\t Old projection..."; strip everything past whitespace.
  const head = String(v).split(/\s/)[0];
  const num = parseFloat(head);
  return Number.isFinite(num) ? num : null;
}

function getPercentilePoints(p: Player): number[] | null {
  if (!p.PERCENTILES || p.PERCENTILES === 'N/A' || typeof p.PERCENTILES === 'string') {
    return null;
  }
  // PERCENTILES is { "1": pts, ..., "100": pts }. Build a sorted array indexed by
  // percentile-1 so we can sample with Math.floor(rand*100).
  const arr: number[] = new Array(100);
  for (let i = 1; i <= 100; i++) {
    const v = (p.PERCENTILES as Record<string, number>)[i] ??
      (p.PERCENTILES as Record<string, number>)[String(i)];
    arr[i - 1] = typeof v === 'number' ? v : NaN;
  }
  // If many entries are NaN, treat as missing.
  if (arr.filter((x) => Number.isFinite(x)).length < 50) return null;
  return arr;
}

function quantile(sorted: Float64Array, q: number): number {
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.floor(q * sorted.length)));
  return sorted[idx];
}

function simulate(starters: Player[]): SimResult {
  const realStarters = starters.filter((p) => p.POS !== 'BN');
  const buckets: number[][] = [];
  let deterministic = 0;
  let pctileCovered = 0;

  for (const p of realStarters) {
    const pts = getPercentilePoints(p);
    if (pts) {
      buckets.push(pts);
      pctileCovered += 1;
      continue;
    }
    // Fall back to VEGAS for DEF/K and players with no sim. We add it as a
    // deterministic constant so totals stay in the right ballpark.
    const vegas = parseVegas(typeof p.VEGAS === 'string' ? p.VEGAS : undefined);
    if (vegas !== null) {
      deterministic += vegas;
    }
  }

  if (buckets.length === 0) {
    return {
      mean: deterministic,
      p10: deterministic,
      p90: deterministic,
      pctileCovered: 0,
      totalSlots: realStarters.length,
      deterministicFloor: deterministic,
    };
  }

  const totals = new Float64Array(N_SIMS);
  for (let i = 0; i < N_SIMS; i++) {
    let sum = deterministic;
    for (let s = 0; s < buckets.length; s++) {
      const arr = buckets[s];
      const idx = (Math.random() * 100) | 0; // 0..99
      sum += arr[idx];
    }
    totals[i] = sum;
  }

  let acc = 0;
  for (let i = 0; i < N_SIMS; i++) acc += totals[i];
  const mean = acc / N_SIMS;

  // Clone + sort for percentiles. Float64Array.sort is in-place + numeric.
  const sorted = totals.slice().sort();
  const p10 = quantile(sorted, 0.1);
  const p90 = quantile(sorted, 0.9);

  return {
    mean,
    p10,
    p90,
    pctileCovered,
    totalSlots: realStarters.length,
    deterministicFloor: deterministic,
  };
}

const LineupConfidence: React.FC<LineupConfidenceProps> = ({ starters }) => {
  const result = useMemo(() => simulate(starters), [starters]);

  if (result.totalSlots === 0) return null;

  const swing = result.p90 - result.p10;

  return (
    <Box
      borderWidth="1px"
      borderRadius="lg"
      bg="white"
      px={4}
      py={3}
      boxShadow="sm"
    >
      <HStack justify="space-between" align="flex-start" wrap="wrap" gap={4}>
        <VStack align="flex-start" spacing={0}>
          <Text fontSize="sm" fontWeight="bold" color="gray.700">
            Lineup Confidence
          </Text>
          <Text fontSize="xs" color="gray.500">
            Monte-Carlo over starters' percentile distributions ({N_SIMS.toLocaleString()} sims).
          </Text>
        </VStack>
        <HStack spacing={6} wrap="wrap">
          <Tooltip label="Average projected league total across all simulations." hasArrow>
            <Stat minW="90px">
              <StatLabel>Projected</StatLabel>
              <StatNumber>{result.mean.toFixed(1)}</StatNumber>
              <StatHelpText fontSize="2xs" color="gray.500">
                mean
              </StatHelpText>
            </Stat>
          </Tooltip>
          <Tooltip label="10% chance you score below this. Floor scenario." hasArrow>
            <Stat minW="90px">
              <StatLabel color="red.600">Floor</StatLabel>
              <StatNumber color="red.600">{result.p10.toFixed(1)}</StatNumber>
              <StatHelpText fontSize="2xs" color="gray.500">
                10th pct
              </StatHelpText>
            </Stat>
          </Tooltip>
          <Tooltip label="10% chance you score above this. Ceiling scenario." hasArrow>
            <Stat minW="90px">
              <StatLabel color="green.600">Ceiling</StatLabel>
              <StatNumber color="green.600">{result.p90.toFixed(1)}</StatNumber>
              <StatHelpText fontSize="2xs" color="gray.500">
                90th pct
              </StatHelpText>
            </Stat>
          </Tooltip>
          <Tooltip label="Spread between ceiling and floor — how volatile this lineup is." hasArrow>
            <Stat minW="90px">
              <StatLabel>Swing</StatLabel>
              <StatNumber>{swing.toFixed(1)}</StatNumber>
              <StatHelpText fontSize="2xs" color="gray.500">
                90th − 10th
              </StatHelpText>
            </Stat>
          </Tooltip>
        </HStack>
      </HStack>
      {result.pctileCovered < result.totalSlots && (
        <Text fontSize="2xs" color="gray.500" mt={2}>
          {result.pctileCovered} / {result.totalSlots} starters had Vegas sim data; the rest contribute
          their projected points as a deterministic constant
          {result.deterministicFloor > 0
            ? ` (+${result.deterministicFloor.toFixed(1)} pts).`
            : '.'}
        </Text>
      )}
    </Box>
  );
};

export default LineupConfidence;
