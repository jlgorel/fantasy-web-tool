/**
 * League Wrapped page — /wrapped/sleeper/:leagueId
 *
 * Renders the schedule-only Phase 1 payload from
 * `GET /wrapped/sleeper/<league_id>?year=YYYY`. Layout:
 *
 *   1. Header: league name, year selector (best-effort: lets the user pick
 *      from the last 4 fantasy years; the backend 404s/errors if a year
 *      has no data, which we surface inline).
 *   2. Hero accolade cards: luckiest, unluckiest, most/least consistent,
 *      best/worst manager, biggest fall-off / come-up.
 *   3. Best-ball records leaderboard.
 *   4. Hypothetical-schedule "what if you played X's slate" matrix.
 *   5. Weekly scoring line chart with the league median as a reference.
 *
 * Phase 2/3 will add roster-moves and draft sections; the existing keys
 * stay intact so this page degrades gracefully against newer payloads.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  Box,
  Heading,
  HStack,
  VStack,
  Text,
  Spinner,
  SimpleGrid,
  Tag,
  Stat,
  StatLabel,
  StatNumber,
  StatHelpText,
  Select,
  Table,
  Thead,
  Tbody,
  Tr,
  Th,
  Td,
  TableContainer,
} from '@chakra-ui/react';
import { api } from '../api/client';
import { LineChart, LineSeries } from '../components/LineChart';
import { SleeperLeagueSeason, WrappedResponse, WrappedStreamersPayload } from '../types/player';


function fallbackYearOptions(): string[] {
  // Used only until the dynamic season chain loads (or if the chain fetch
  // fails). Mirrors the backend's 4-year probe window so the dropdown
  // still shows reasonable choices in the meantime.
  const now = new Date();
  const fantasyYear =
    now.getMonth() < 2 ? now.getFullYear() - 1 : now.getFullYear();
  return [0, 1, 2, 3].map((i) => String(fantasyYear - i));
}


// Distinct hues for each league user's weekly-scoring line. We cycle through
// these — most leagues are 8-12 teams so we rarely run out of unique colors.
const SERIES_COLORS = [
  '#3182ce', '#38a169', '#dd6b20', '#d53f8c',
  '#805ad5', '#319795', '#e53e3e', '#d69e2e',
  '#2d3748', '#718096', '#4299e1', '#48bb78',
];


const accoladeCard = (
  title: string,
  username: string | null | undefined,
  helper?: string,
): JSX.Element => (
  <Box borderWidth={1} borderRadius="md" p={3} bg="white">
    <Stat>
      <StatLabel>{title}</StatLabel>
      <StatNumber fontSize="xl">{username ?? '—'}</StatNumber>
      {helper && <StatHelpText>{helper}</StatHelpText>}
    </Stat>
  </Box>
);


type StreamersSortKey = 'username' | 'k_avg' | 'def_avg' | 'combined_avg';


const StreamersSection: React.FC<{ data: WrappedStreamersPayload }> = ({
  data,
}) => {
  const hasK = data.positions_included.includes('K');
  const hasDef = data.positions_included.includes('DEF');
  const hasCombined = hasK && hasDef;

  // Default sort: combined when both positions exist, else whichever is
  // present. Highest first (combined / k / def all "bigger is better").
  const defaultSort: StreamersSortKey = hasCombined
    ? 'combined_avg'
    : hasK
      ? 'k_avg'
      : 'def_avg';
  const [sortKey, setSortKey] = useState<StreamersSortKey>(defaultSort);
  const [sortDesc, setSortDesc] = useState<boolean>(true);

  const rows = useMemo(() => {
    const entries = Object.entries(data.by_user).map(([username, e]) => ({
      username,
      ...e,
    }));
    const cmp = (
      a: typeof entries[number],
      b: typeof entries[number],
    ): number => {
      if (sortKey === 'username') {
        return a.username.localeCompare(b.username);
      }
      const av = a[sortKey];
      const bv = b[sortKey];
      // Nulls sort to the bottom regardless of direction.
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return av - bv;
    };
    entries.sort((a, b) => (sortDesc ? -cmp(a, b) : cmp(a, b)));
    return entries;
  }, [data.by_user, sortKey, sortDesc]);

  const onSort = (key: StreamersSortKey) => {
    if (sortKey === key) {
      setSortDesc(!sortDesc);
    } else {
      setSortKey(key);
      // First click on a numeric column = descending (best on top).
      // First click on username = ascending (A→Z).
      setSortDesc(key !== 'username');
    }
  };

  const fmt = (v: number | null): string => (v == null ? '—' : v.toFixed(2));
  const arrow = (key: StreamersSortKey) =>
    sortKey === key ? (sortDesc ? ' ▼' : ' ▲') : '';

  return (
    <Box bg="white" borderWidth={1} borderRadius="md" p={3}>
      <Heading size="sm" mb={2}>
        Best streamers
      </Heading>
      <Text fontSize="xs" color="gray.500" mb={3}>
        Average starter points at{' '}
        {data.positions_included.join(' + ')} per week. Bench-quality starts
        (zeroes, byes you forgot about) count too — that's the whole point.
      </Text>

      <SimpleGrid
        columns={{ base: 1, sm: hasCombined ? 3 : 2 }}
        gap={3}
        mb={3}
      >
        {hasK &&
          accoladeCard(
            'Best kicker streamer',
            data.best_kicker?.username,
            data.best_kicker
              ? `${data.best_kicker.average.toFixed(2)} pts/wk`
              : undefined,
          )}
        {hasDef &&
          accoladeCard(
            'Best defense streamer',
            data.best_defense?.username,
            data.best_defense
              ? `${data.best_defense.average.toFixed(2)} pts/wk`
              : undefined,
          )}
        {hasCombined &&
          accoladeCard(
            'Best combined K+DEF',
            data.best_combined?.username,
            data.best_combined
              ? `${data.best_combined.average.toFixed(2)} pts/wk`
              : undefined,
          )}
      </SimpleGrid>

      <TableContainer>
        <Table
          size="sm"
          variant="simple"
          sx={{ 'th, td': { px: 2, py: 1.5, fontSize: 'xs' } }}
        >
          <Thead>
            <Tr>
              <Th
                cursor="pointer"
                onClick={() => onSort('username')}
                userSelect="none"
              >
                Manager{arrow('username')}
              </Th>
              {hasK && (
                <Th
                  isNumeric
                  cursor="pointer"
                  onClick={() => onSort('k_avg')}
                  userSelect="none"
                >
                  K avg{arrow('k_avg')}
                </Th>
              )}
              {hasDef && (
                <Th
                  isNumeric
                  cursor="pointer"
                  onClick={() => onSort('def_avg')}
                  userSelect="none"
                >
                  DEF avg{arrow('def_avg')}
                </Th>
              )}
              {hasCombined && (
                <Th
                  isNumeric
                  cursor="pointer"
                  onClick={() => onSort('combined_avg')}
                  userSelect="none"
                >
                  Combined{arrow('combined_avg')}
                </Th>
              )}
              <Th isNumeric>Wks</Th>
            </Tr>
          </Thead>
          <Tbody>
            {rows.map((row) => (
              <Tr key={row.username}>
                <Td>{row.username}</Td>
                {hasK && <Td isNumeric>{fmt(row.k_avg)}</Td>}
                {hasDef && <Td isNumeric>{fmt(row.def_avg)}</Td>}
                {hasCombined && (
                  <Td isNumeric>{fmt(row.combined_avg)}</Td>
                )}
                <Td isNumeric>{row.weeks_counted}</Td>
              </Tr>
            ))}
          </Tbody>
        </Table>
      </TableContainer>
    </Box>
  );
};


const WrappedPage: React.FC = () => {
  const { leagueId } = useParams<{ leagueId: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  // Dynamic year list — fetched once per leagueId from the season-chain
  // endpoint so leagues founded in 2018 see all seven years and brand-new
  // leagues only see the year they exist in. Falls back to a static
  // 4-year window before the fetch resolves (or if it errors out).
  const fallbackYears = useMemo(fallbackYearOptions, []);
  const [seasonChain, setSeasonChain] = useState<SleeperLeagueSeason[] | null>(
    null,
  );
  const yearOptions = useMemo(() => {
    if (seasonChain && seasonChain.length > 0) {
      return seasonChain.map((s) => s.season);
    }
    return fallbackYears;
  }, [seasonChain, fallbackYears]);
  // Year is sourced from the URL so the page is shareable and so navigating
  // to a new (resolved) league_id keeps the user on the year they picked.
  const urlYear = searchParams.get('year');
  const year =
    urlYear && yearOptions.includes(urlYear) ? urlYear : yearOptions[0];
  const [payload, setPayload] = useState<WrappedResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [resolving, setResolving] = useState(false);

  // Fetch the league's full season chain once we know the league_id. This
  // populates the year dropdown with only the years that actually exist
  // for this league. Best-effort — if it fails we fall back to the static
  // year window and let the per-year fetch surface "no data" inline.
  useEffect(() => {
    if (!leagueId) return;
    let cancelled = false;
    api
      .getSleeperLeagueSeasons(leagueId)
      .then((resp) => {
        if (!cancelled) setSeasonChain(resp.seasons);
      })
      .catch((err) => {
        console.warn('getSleeperLeagueSeasons failed; using fallback', err);
      });
    return () => {
      cancelled = true;
    };
  }, [leagueId]);

  // When the user picks a different year we walk the previous_league_id
  // chain server-side to find the matching season's league_id, then
  // navigate to that URL. This keeps shareable URLs honest — the league_id
  // in the path always corresponds to the data being shown.
  const handleYearChange = async (newYear: string) => {
    if (!leagueId || newYear === year) return;
    setResolving(true);
    setError(null);
    try {
      const resp = await api.resolveSleeperLeague(leagueId, newYear);
      if (resp.league_id) {
        navigate(
          `/wrapped/sleeper/${encodeURIComponent(resp.league_id)}?year=${encodeURIComponent(newYear)}`,
        );
      } else {
        setError(`This league doesn't have a ${newYear} season on Sleeper.`);
      }
    } catch (err) {
      console.error('resolveSleeperLeague failed', err);
      setError(`Could not resolve a ${newYear} league_id for this league.`);
    } finally {
      setResolving(false);
    }
  };

  useEffect(() => {
    if (!leagueId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getWrappedSleeper(leagueId, year)
      .then((data) => {
        if (!cancelled) setPayload(data);
      })
      .catch((err) => {
        console.error('Wrapped fetch failed', err);
        if (!cancelled) {
          setError(
            err?.status === 404
              ? `No Wrapped data found for league ${leagueId} in ${year}.`
              : 'Could not load Wrapped. Try a different year.',
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
  }, [leagueId, year]);

  const weeklySeries: LineSeries[] = useMemo(() => {
    if (!payload) return [];
    const { weekly_scores } = payload.schedule;
    return Object.entries(weekly_scores).map(([user, byWeek], i) => ({
      label: user,
      color: SERIES_COLORS[i % SERIES_COLORS.length],
      points: Object.entries(byWeek)
        .map(([w, pts]) => ({ x: parseInt(w, 10), y: Number(pts) }))
        .filter((p) => Number.isFinite(p.x))
        .sort((a, b) => a.x - b.x),
    }));
  }, [payload]);

  // Median scores derived from the schedule payload — drawn as a single
  // reference line on the weekly chart (averaged so it's a flat overlay
  // rather than a per-week wiggle, which is hard to read at this size).
  const medianAvg = useMemo(() => {
    if (!payload) return null;
    const vals = Object.values(payload.schedule.median_scores);
    if (!vals.length) return null;
    return vals.reduce((a, b) => a + Number(b), 0) / vals.length;
  }, [payload]);

  // Best-ball leaderboard sorted by wins desc.
  const bestBallRanking = useMemo(() => {
    if (!payload) return [];
    return Object.entries(payload.schedule.best_ball_records)
      .map(([user, rec]) => ({ user, ...rec }))
      .sort((a, b) => b.wins - a.wins);
  }, [payload]);

  return (
    <Box p={{ base: 3, md: 6 }} maxW={{ base: '100%', xl: '1400px' }} mx="auto">
      <VStack align="stretch" gap={4}>
        <HStack justify="space-between" wrap="wrap" gap={3}>
          <Box>
            <Heading size="lg">League Wrapped</Heading>
            {payload?.meta?.league_name && (
              <Text color="gray.600">
                {payload.meta.league_name}
                {payload.meta.is_dynasty && (
                  <Tag ml={2} colorScheme="purple" size="sm">
                    Dynasty
                  </Tag>
                )}
              </Text>
            )}
          </Box>
          <HStack>
            <Text fontSize="sm" color="gray.600">
              Season:
            </Text>
            <Select
              value={year}
              onChange={(e) => handleYearChange(e.target.value)}
              size="sm"
              width="auto"
              isDisabled={resolving}
            >
              {yearOptions.map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </Select>
          </HStack>
        </HStack>

        {loading && (
          <Box py={8} textAlign="center">
            <Spinner size="lg" />
          </Box>
        )}

        {!loading && error && (
          <Box bg="red.50" borderWidth={1} borderColor="red.200" borderRadius="md" p={4}>
            <Text color="red.700">{error}</Text>
          </Box>
        )}

        {!loading && !error && payload && (
          <>
            {/* Hero accolades */}
            <SimpleGrid columns={{ base: 1, sm: 2, md: 4 }} gap={3}>
              {accoladeCard(
                'Luckiest',
                payload.schedule.luck.luckiest.username,
                payload.schedule.luck.luckiest.username
                  ? `${payload.schedule.luck.luckiest.count} wins below median`
                  : undefined,
              )}
              {accoladeCard(
                'Unluckiest',
                payload.schedule.luck.unluckiest.username,
                payload.schedule.luck.unluckiest.username
                  ? `${payload.schedule.luck.unluckiest.count} losses above median`
                  : undefined,
              )}
              {accoladeCard(
                'Most consistent',
                payload.schedule.consistency.most_consistent?.username,
                payload.schedule.consistency.most_consistent
                  ? `MAD ${payload.schedule.consistency.most_consistent.mad.toFixed(1)} pts`
                  : undefined,
              )}
              {accoladeCard(
                'Least consistent',
                payload.schedule.consistency.least_consistent?.username,
                payload.schedule.consistency.least_consistent
                  ? `MAD ${payload.schedule.consistency.least_consistent.mad.toFixed(1)} pts`
                  : undefined,
              )}
              {accoladeCard(
                'Best manager',
                payload.schedule.manager_efficiency.most_efficient?.username,
                payload.schedule.manager_efficiency.most_efficient
                  ? `${payload.schedule.manager_efficiency.most_efficient.efficiency_pct.toFixed(1)}% of best-ball`
                  : undefined,
              )}
              {accoladeCard(
                'Worst manager',
                payload.schedule.manager_efficiency.least_efficient?.username,
                payload.schedule.manager_efficiency.least_efficient
                  ? `${payload.schedule.manager_efficiency.least_efficient.efficiency_pct.toFixed(1)}% of best-ball`
                  : undefined,
              )}
              {accoladeCard(
                'Biggest come-up',
                payload.schedule.falloff_comeup.biggest_come_up?.username,
                payload.schedule.falloff_comeup.biggest_come_up
                  ? `+${payload.schedule.falloff_comeup.biggest_come_up.delta.toFixed(1)} pts/wk`
                  : undefined,
              )}
              {accoladeCard(
                'Biggest fall-off',
                payload.schedule.falloff_comeup.biggest_falloff?.username,
                payload.schedule.falloff_comeup.biggest_falloff
                  ? `−${payload.schedule.falloff_comeup.biggest_falloff.delta.toFixed(1)} pts/wk`
                  : undefined,
              )}
            </SimpleGrid>

            {/* Best-ball leaderboard */}
            <Box bg="white" borderWidth={1} borderRadius="md" p={3}>
              <Heading size="sm" mb={2}>
                Best-ball season standings
              </Heading>
              <Text fontSize="xs" color="gray.500" mb={2}>
                Each week, you "win" against everyone whose best-possible lineup
                scored less than yours. Sums across all played weeks.
              </Text>
              <TableContainer>
                <Table size="sm" variant="simple">
                  <Thead>
                    <Tr>
                      <Th>Manager</Th>
                      <Th isNumeric>Wins</Th>
                      <Th isNumeric>Losses</Th>
                    </Tr>
                  </Thead>
                  <Tbody>
                    {bestBallRanking.map(({ user, wins, losses }) => (
                      <Tr key={user}>
                        <Td>{user}</Td>
                        <Td isNumeric>{wins}</Td>
                        <Td isNumeric>{losses}</Td>
                      </Tr>
                    ))}
                  </Tbody>
                </Table>
              </TableContainer>
            </Box>

            {/* Hypothetical schedule matrix */}
            <Box bg="white" borderWidth={1} borderRadius="md" p={3}>
              <Heading size="sm" mb={2}>
                What if you had ___'s schedule?
              </Heading>
              <Text fontSize="xs" color="gray.500" mb={2}>
                Rows = your scores. Columns = whose schedule you "borrow". Diagonal
                cells are everyone's actual record.
              </Text>
              <TableContainer maxW="100%" overflowX="auto">
                <Table
                  size="sm"
                  variant="simple"
                  sx={{
                    // Tight padding so a 12-team matrix (13 cols) fits the
                    // page width without horizontal scrolling.
                    'th, td': { px: 1.5, py: 1, fontSize: 'xs' },
                  }}
                >
                  <Thead>
                    <Tr>
                      <Th>You ↓ / Schedule of →</Th>
                      {payload.meta.users.map((u) => (
                        <Th key={u} isNumeric>
                          {u}
                        </Th>
                      ))}
                    </Tr>
                  </Thead>
                  <Tbody>
                    {payload.meta.users.map((rowUser) => {
                      const row = payload.schedule.hypothetical_matrix[rowUser] ?? {};
                      return (
                        <Tr key={rowUser}>
                          <Td fontWeight="semibold">{rowUser}</Td>
                          {payload.meta.users.map((colUser) => {
                            const rec = row[colUser];
                            const isDiag = rowUser === colUser;
                            return (
                              <Td
                                key={colUser}
                                isNumeric
                                bg={isDiag ? 'blue.50' : undefined}
                              >
                                {rec ? `${rec.wins}-${rec.losses}` : '—'}
                              </Td>
                            );
                          })}
                        </Tr>
                      );
                    })}
                  </Tbody>
                </Table>
              </TableContainer>
            </Box>

            {/* Weekly scores chart */}
            <Box bg="white" borderWidth={1} borderRadius="md" p={3}>
              <Heading size="sm" mb={2}>
                Weekly scores
              </Heading>
              <LineChart
                series={weeklySeries}
                xLabel="Week"
                yLabel="Points"
                yMin={0}
                refLine={
                  medianAvg != null
                    ? { y: medianAvg, label: `Avg median ${medianAvg.toFixed(1)}` }
                    : undefined
                }
              />
            </Box>

            {/* Phase 2: Roster moves */}
            {payload.roster_moves && (
              <Box bg="white" borderWidth={1} borderRadius="md" p={3}>
                <Heading size="sm" mb={2}>
                  Roster moves
                </Heading>
                <Text fontSize="xs" color="gray.500" mb={3}>
                  Troll = the player you started over a higher-scoring bench
                  player most often. Add/drop accolades use value over the
                  league's positional replacement-level player.
                </Text>
                <TableContainer>
                  <Table
                    size="sm"
                    variant="simple"
                    sx={{
                      // Tight padding so 6 columns of accolades fit a
                      // 10/12-team league at typical desktop widths.
                      'th, td': { px: 2, py: 1.5, fontSize: 'xs' },
                    }}
                  >
                    <Thead>
                      <Tr>
                        <Th>Manager</Th>
                        <Th>Biggest troll</Th>
                        <Th>Best waiver add</Th>
                        <Th>Worst drop</Th>
                        <Th>Earliest pickup</Th>
                        <Th>Held longest</Th>
                      </Tr>
                    </Thead>
                    <Tbody>
                      {payload.meta.users.map((user) => {
                        const moves = payload.roster_moves!.by_user[user];
                        const troll = payload.roster_moves!.troll[user];
                        const bestWorstByPos = moves?.worst_drop ?? {};
                        const worstByVal = Object.values(bestWorstByPos).sort(
                          (a, b) => b.value_over_baseline - a.value_over_baseline,
                        )[0];
                        return (
                          <Tr key={user}>
                            <Td fontWeight="semibold">{user}</Td>
                            <Td>
                              {troll ? (
                                <>
                                  {troll.name}
                                  <Text fontSize="xs" color="gray.500">
                                    bench {troll.bench_avg.toFixed(1)} vs start{' '}
                                    {troll.start_avg.toFixed(1)} (×{troll.num_start})
                                  </Text>
                                </>
                              ) : (
                                '—'
                              )}
                            </Td>
                            <Td>
                              {moves?.best_add ? (
                                <>
                                  {moves.best_add.name}
                                  <Text fontSize="xs" color="gray.500">
                                    +{moves.best_add.value_over_baseline.toFixed(1)} vs baseline
                                  </Text>
                                </>
                              ) : (
                                '—'
                              )}
                            </Td>
                            <Td>
                              {worstByVal ? (
                                <>
                                  {worstByVal.name}
                                  <Text fontSize="xs" color="gray.500">
                                    +{worstByVal.value_over_baseline.toFixed(1)} vs baseline
                                  </Text>
                                </>
                              ) : (
                                '—'
                              )}
                            </Td>
                            <Td>
                              {moves?.early_pickup ? (
                                <>
                                  {moves.early_pickup.name}
                                  <Text fontSize="xs" color="gray.500">
                                    Wk {moves.early_pickup.week_added} @{' '}
                                    {moves.early_pickup.owned_pct_when_added.toFixed(0)}% owned
                                  </Text>
                                </>
                              ) : (
                                '—'
                              )}
                            </Td>
                            <Td>
                              {moves?.late_drop ? (
                                <>
                                  {moves.late_drop.name}
                                  <Text fontSize="xs" color="gray.500">
                                    Dropped Wk {moves.late_drop.week_dropped} @{' '}
                                    {moves.late_drop.owned_pct_at_drop.toFixed(0)}% owned
                                  </Text>
                                </>
                              ) : (
                                '—'
                              )}
                            </Td>
                          </Tr>
                        );
                      })}
                    </Tbody>
                  </Table>
                </TableContainer>
              </Box>
            )}

            {/* Phase 3: Draft */}
            {payload.draft && Object.keys(payload.draft.by_user).length > 0 && (
              <Box bg="white" borderWidth={1} borderRadius="md" p={3}>
                <Heading size="sm" mb={2}>
                  Draft grades
                </Heading>
                <Text fontSize="xs" color="gray.500" mb={3}>
                  Value-over-slot = where a player was drafted at his
                  position vs. where he actually finished. Positive = steal,
                  negative = bust.
                </Text>

                {/* Hero accolades for the draft */}
                <SimpleGrid columns={{ base: 1, sm: 2, md: 3 }} gap={3} mb={3}>
                  {accoladeCard(
                    'Biggest steal',
                    payload.draft.biggest_steal?.name,
                    payload.draft.biggest_steal
                      ? `Pick ${payload.draft.biggest_steal.pick_no} • ${payload.draft.biggest_steal.position}${payload.draft.biggest_steal.actual_pos_rank} (drafted ${payload.draft.biggest_steal.position}${payload.draft.biggest_steal.drafted_pos_rank})`
                      : undefined,
                  )}
                  {accoladeCard(
                    'Biggest bust',
                    payload.draft.biggest_bust?.name,
                    payload.draft.biggest_bust
                      ? `Pick ${payload.draft.biggest_bust.pick_no} • ${payload.draft.biggest_bust.position}${payload.draft.biggest_bust.actual_pos_rank} (drafted ${payload.draft.biggest_bust.position}${payload.draft.biggest_bust.drafted_pos_rank})`
                      : undefined,
                  )}
                  {accoladeCard(
                    'Mr. Irrelevant Hero',
                    payload.draft.mr_irrelevant_hero?.name,
                    payload.draft.mr_irrelevant_hero
                      ? `Pick ${payload.draft.mr_irrelevant_hero.pick_no} by ${payload.draft.mr_irrelevant_hero.username}`
                      : undefined,
                  )}
                </SimpleGrid>

                <TableContainer>
                  <Table
                    size="sm"
                    variant="simple"
                    sx={{ 'th, td': { px: 2, py: 1.5, fontSize: 'xs' } }}
                  >
                    <Thead>
                      <Tr>
                        <Th>Manager</Th>
                        <Th>Best pick</Th>
                        <Th>Worst pick</Th>
                      </Tr>
                    </Thead>
                    <Tbody>
                      {payload.meta.users.map((user) => {
                        const rec = payload.draft!.by_user[user];
                        if (!rec) return null;
                        return (
                          <Tr key={user}>
                            <Td fontWeight="semibold">{user}</Td>
                            <Td>
                              {rec.best_pick ? (
                                <>
                                  {rec.best_pick.name}
                                  <Text fontSize="xs" color="gray.500">
                                    Pick {rec.best_pick.pick_no} •{' '}
                                    {rec.best_pick.position}
                                    {rec.best_pick.actual_pos_rank} (drafted{' '}
                                    {rec.best_pick.position}
                                    {rec.best_pick.drafted_pos_rank})
                                  </Text>
                                </>
                              ) : (
                                '—'
                              )}
                            </Td>
                            <Td>
                              {rec.worst_pick ? (
                                <>
                                  {rec.worst_pick.name}
                                  <Text fontSize="xs" color="gray.500">
                                    Pick {rec.worst_pick.pick_no} •{' '}
                                    {rec.worst_pick.position}
                                    {rec.worst_pick.actual_pos_rank} (drafted{' '}
                                    {rec.worst_pick.position}
                                    {rec.worst_pick.drafted_pos_rank})
                                  </Text>
                                </>
                              ) : (
                                '—'
                              )}
                            </Td>
                          </Tr>
                        );
                      })}
                    </Tbody>
                  </Table>
                </TableContainer>
              </Box>
            )}

            {/* Phase 3: Trades */}
            {payload.trades && payload.trades.trades.length > 0 && (
              <Box bg="white" borderWidth={1} borderRadius="md" p={3}>
                <Heading size="sm" mb={2}>
                  Trade ledger
                </Heading>
                <Text fontSize="xs" color="gray.500" mb={3}>
                  Player values are FantasyCalc trade values for this league
                  shape ({payload.meta.is_dynasty ? 'dynasty' : 'redraft'},{' '}
                  {payload.meta.num_qbs}-QB). Future draft picks use a flat
                  per-round value.
                </Text>

                <SimpleGrid columns={{ base: 1, sm: 2 }} gap={3} mb={3}>
                  {accoladeCard(
                    'Biggest fleecing',
                    payload.trades.biggest_fleecing?.winner,
                    payload.trades.biggest_fleecing
                      ? `+${payload.trades.biggest_fleecing.value_gap.toFixed(0)} value, Wk ${payload.trades.biggest_fleecing.week}`
                      : undefined,
                  )}
                  {accoladeCard(
                    'Most active trader',
                    payload.trades.most_active_trader?.username,
                    payload.trades.most_active_trader
                      ? `${payload.trades.most_active_trader.num_trades} trade${payload.trades.most_active_trader.num_trades === 1 ? '' : 's'}`
                      : undefined,
                  )}
                </SimpleGrid>

                {/* Per-trade list */}
                <TableContainer>
                  <Table
                    size="sm"
                    variant="simple"
                    sx={{ 'th, td': { px: 2, py: 1.5, fontSize: 'xs', verticalAlign: 'top' } }}
                  >
                    <Thead>
                      <Tr>
                        <Th>Wk</Th>
                        <Th>Sides</Th>
                        <Th isNumeric>Gap</Th>
                      </Tr>
                    </Thead>
                    <Tbody>
                      {payload.trades.trades.map((trade) => (
                        <Tr key={trade.transaction_id || `${trade.week}-${trade.winner}`}>
                          <Td>{trade.week}</Td>
                          <Td>
                            <VStack align="stretch" gap={1}>
                              {trade.sides.map((side) => (
                                <Box key={side.username}>
                                  <Text
                                    fontWeight={
                                      side.username === trade.winner
                                        ? 'semibold'
                                        : 'normal'
                                    }
                                  >
                                    {side.username} got{' '}
                                    {[
                                      ...side.players.map((p) => p.name),
                                      ...side.picks.map(
                                        (pk) =>
                                          `${pk.season ?? '?'} R${pk.round ?? '?'} pick`,
                                      ),
                                    ].join(', ') || '—'}
                                  </Text>
                                  <Text fontSize="2xs" color="gray.500">
                                    Value {side.total_value.toFixed(0)}
                                  </Text>
                                </Box>
                              ))}
                            </VStack>
                          </Td>
                          <Td isNumeric>
                            {trade.value_gap > 0
                              ? `+${trade.value_gap.toFixed(0)}`
                              : '—'}
                          </Td>
                        </Tr>
                      ))}
                    </Tbody>
                  </Table>
                </TableContainer>
              </Box>
            )}

            {/* Phase 4: Best streamers */}
            {payload.streamers &&
              payload.streamers.positions_included.length > 0 && (
                <StreamersSection data={payload.streamers} />
              )}
          </>
        )}
      </VStack>
    </Box>
  );
};

export default WrappedPage;
