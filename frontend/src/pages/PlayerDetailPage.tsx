/**
 * Player Detail Page — /player/:playerId
 *
 * Renders the aggregated payload from `GET /player/<pid>`:
 *   - Header with name + position chip(s).
 *   - Year tabs derived from `available_years`.
 *   - Per-year season summary (points + position rank).
 *   - Weekly fantasy-points line chart (half-PPR / PPR / standard).
 *   - Weekly ownership + start-rate line chart.
 *
 * The payload is intentionally already-aggregated by the backend so this
 * page is mostly presentation; we just project per-year slices into the
 * chart helpers.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box,
  Heading,
  HStack,
  VStack,
  Text,
  Tag,
  Spinner,
  Button,
  SimpleGrid,
  Stat,
  StatLabel,
  StatNumber,
  StatHelpText,
  Tabs,
  TabList,
  Tab,
  TabPanels,
  TabPanel,
} from '@chakra-ui/react';
import { api } from '../api/client';
import { LineChart, LineSeries } from '../components/LineChart';
import {
  PlayerDetailResponse,
} from '../types/player';


function buildOwnershipSeries(
  ownershipForYear: { [w: string]: { owned: number; started: number } } | undefined,
): LineSeries[] {
  if (!ownershipForYear) return [];
  const weeks = Object.keys(ownershipForYear)
    .map((w) => parseInt(w, 10))
    .filter((w) => Number.isFinite(w))
    .sort((a, b) => a - b);

  return [
    {
      label: 'Owned %',
      color: '#805ad5',
      points: weeks.map((w) => ({
        x: w,
        y: Number(ownershipForYear[String(w)]?.owned ?? 0),
      })),
    },
    {
      label: 'Started %',
      color: '#d53f8c',
      points: weeks.map((w) => ({
        x: w,
        y: Number(ownershipForYear[String(w)]?.started ?? 0),
      })),
    },
  ];
}

const PlayerDetailPage: React.FC = () => {
  const { playerId } = useParams<{ playerId: string }>();
  const navigate = useNavigate();
  const [payload, setPayload] = useState<PlayerDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeYearIdx, setActiveYearIdx] = useState(0);

  useEffect(() => {
    if (!playerId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getPlayerDetail(playerId)
      .then((data) => {
        if (!cancelled) setPayload(data);
      })
      .catch((err) => {
        console.error('Player detail fetch failed', err);
        if (!cancelled) {
          setError(
            err?.status === 404
              ? `No data for player "${playerId}". They may have never been on a Sleeper roster we tracked.`
              : 'Could not load player detail. Try again in a moment.',
          );
          setPayload(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [playerId]);

  // Years come back from the backend already filtered to those with data;
  // we display newest first so the most relevant tab is the default.
  const years = useMemo(
    () => (payload?.available_years ?? []).slice().sort().reverse(),
    [payload],
  );

  if (loading) {
    return (
      <Box p={8} textAlign="center">
        <Spinner size="lg" />
      </Box>
    );
  }

  if (error || !payload) {
    return (
      <Box p={6} maxW="640px" mx="auto">
        <Heading size="md" mb={3}>
          Player not found
        </Heading>
        <Text color="gray.600" mb={4}>
          {error ?? 'Player payload was empty.'}
        </Text>
        <Button onClick={() => navigate(-1)} colorScheme="blue" variant="outline">
          Go back
        </Button>
      </Box>
    );
  }

  const { meta, scoring, ownership } = payload;

  return (
    <Box p={{ base: 3, md: 6 }} maxW="980px" mx="auto">
      <VStack align="stretch" gap={4}>
        {/* Header */}
        <Box>
          <Heading size="lg">{meta.full_name ?? `Player ${meta.player_id}`}</Heading>
          <HStack mt={2} gap={2}>
            {meta.fantasy_positions.map((pos) => (
              <Tag key={pos} size="md" colorScheme="blue">
                {pos}
              </Tag>
            ))}
            <Text fontSize="sm" color="gray.500">
              ID: {meta.player_id}
            </Text>
          </HStack>
        </Box>

        {years.length === 0 ? (
          <Text color="gray.600">
            No scoring or ownership history is available for this player yet.
          </Text>
        ) : (
          <Tabs
            index={activeYearIdx}
            onChange={setActiveYearIdx}
            colorScheme="blue"
            variant="enclosed"
          >
            <TabList>
              {years.map((y) => (
                <Tab key={y}>{y}</Tab>
              ))}
            </TabList>
            <TabPanels>
              {years.map((year) => {
                const yearScoring = scoring[year];
                const yearOwnership = ownership[year];
                const ownershipSeries = buildOwnershipSeries(yearOwnership);
                const season = yearScoring?.season;

                return (
                  <TabPanel key={year} px={0}>
                    {season && (
                      <SimpleGrid columns={{ base: 2, md: 4 }} gap={3} mb={4}>
                        <Stat>
                          <StatLabel>½ PPR</StatLabel>
                          <StatNumber>{Number(season.half_ppr_points ?? 0).toFixed(1)}</StatNumber>
                          {season.half_ppr_rank != null && (
                            <StatHelpText>Rank #{season.half_ppr_rank}</StatHelpText>
                          )}
                        </Stat>
                        <Stat>
                          <StatLabel>PPR</StatLabel>
                          <StatNumber>{Number(season.ppr_points ?? 0).toFixed(1)}</StatNumber>
                          {season.ppr_rank != null && (
                            <StatHelpText>Rank #{season.ppr_rank}</StatHelpText>
                          )}
                        </Stat>
                        <Stat>
                          <StatLabel>Standard</StatLabel>
                          <StatNumber>{Number(season.std_points ?? 0).toFixed(1)}</StatNumber>
                          {season.std_rank != null && (
                            <StatHelpText>Rank #{season.std_rank}</StatHelpText>
                          )}
                        </Stat>
                        {season.receptions != null && (
                          <Stat>
                            <StatLabel>Receptions</StatLabel>
                            <StatNumber>{season.receptions}</StatNumber>
                          </Stat>
                        )}
                      </SimpleGrid>
                    )}

                    {ownershipSeries.some((s) => s.points.length > 0) ? (
                      <Box bg="white" borderWidth={1} borderRadius="md" p={3}>
                        <Heading size="sm" mb={2}>
                          Ownership &amp; start rate
                        </Heading>
                        <LineChart
                          series={ownershipSeries}
                          xLabel="Week"
                          yLabel="%"
                          yMin={0}
                          yMax={100}
                        />
                      </Box>
                    ) : (
                      <Text color="gray.500">
                        No ownership history captured for {year}.
                      </Text>
                    )}
                  </TabPanel>
                );
              })}
            </TabPanels>
          </Tabs>
        )}
      </VStack>
    </Box>
  );
};

export default PlayerDetailPage;
