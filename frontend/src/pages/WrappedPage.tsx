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
  Accordion,
  AccordionItem,
  AccordionButton,
  AccordionPanel,
  AccordionIcon,
} from '@chakra-ui/react';
import { api } from '../api/client';
import { LineChart, LineSeries } from '../components/LineChart';
import { TradeInspector } from '../components/TradeInspector';
import { TradeInspectorRedraft } from '../components/TradeInspectorRedraft';
import { SleeperLeagueSeason, WrappedApiResponse, WrappedAllTimeAccolades, WrappedAllTimeResponse, WrappedResponse, WrappedStreamersPayload } from '../types/player';


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


/**
 * Renders the full per-season Wrapped content (hero accolades, best-ball
 * leaderboard, hypothetical-schedule matrix, weekly chart, roster_moves /
 * draft / trades / streamers sections) for one season's payload.
 *
 * Extracted out of ``WrappedPage`` so the all-time view (TODO #5) can reuse
 * the same render code for each year inside its accordion. Pure over its
 * ``payload`` prop — no fetching, no router state.
 */
const YearSections: React.FC<{ payload: WrappedResponse }> = ({ payload }) => {
  // Single-row expansion state for the trade ledger: only one expanded
  // at a time per spec, so a string id (or null) is enough.
  const [expandedTradeId, setExpandedTradeId] = useState<string | null>(null);

  const weeklySeries: LineSeries[] = useMemo(() => {
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
    const vals = Object.values(payload.schedule.median_scores);
    if (!vals.length) return null;
    return vals.reduce((a, b) => a + Number(b), 0) / vals.length;
  }, [payload]);

  // Best-ball leaderboard sorted by wins desc.
  const bestBallRanking = useMemo(() => {
    return Object.entries(payload.schedule.best_ball_records)
      .map(([user, rec]) => ({ user, ...rec }))
      .sort((a, b) => b.wins - a.wins);
  }, [payload]);

  return (
    <VStack align="stretch" gap={4}>
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
            {payload.meta.is_dynasty ? (
              <>
                Trades are evaluated by integrating each side's KTC value
                over the time it was held, so a trade that aged badly
                registers as a loss even if it looked even at the deadline.
                Numbers are in <strong>KTC equivalent points</strong>{' '}
                (familiar 0-9999 scale) — click any row to see the running
                value chart.
              </>
            ) : (
              <>
                Trades are retro-scored over the rest of the season using{' '}
                <strong>VORP</strong> (value over replacement): each player's
                points after the trade minus what a replacement-level starter
                would have produced. Click any row to see the per-asset
                breakdown.
              </>
            )}
          </Text>

          <SimpleGrid columns={{ base: 1, sm: 2 }} gap={3} mb={3}>
            {accoladeCard(
              payload.meta.is_dynasty ? 'Biggest fleecing' : 'Biggest steal',
              payload.trades.biggest_fleecing?.winner,
              payload.trades.biggest_fleecing
                ? payload.meta.is_dynasty
                  ? `+${payload.trades.biggest_fleecing.ktc_edge_per_season.toFixed(0)} KTC/yr, Wk ${payload.trades.biggest_fleecing.week}`
                  : `+${payload.trades.biggest_fleecing.ktc_edge_per_season.toFixed(1)} VORP, Wk ${payload.trades.biggest_fleecing.week}`
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

          {/* Per-trade list. Rows are clickable and only one trade
              expands at a time — keeps the visual focus tight, the
              page short, and the network calls cheap (one inspector
              fetch per click, cached server-side for 24h). */}
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
                  <Th isNumeric>
                    {payload.meta.is_dynasty ? 'KTC edge / yr' : 'VORP edge'}
                  </Th>
                </Tr>
              </Thead>
              <Tbody>
                {payload.trades.trades.map((trade) => {
                  const rowKey =
                    trade.transaction_id || `${trade.week}-${trade.winner}`;
                  const isOpen = expandedTradeId === rowKey;
                  return (
                    <React.Fragment key={rowKey}>
                      <Tr
                        onClick={() =>
                          setExpandedTradeId(isOpen ? null : rowKey)
                        }
                        cursor="pointer"
                        _hover={{ bg: 'gray.50' }}
                        bg={isOpen ? 'blue.50' : undefined}
                      >
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
                                  {side.assets
                                    .map((a) => a.label)
                                    .join(', ') || '—'}
                                </Text>
                                <Text fontSize="2xs" color="gray.500">
                                  {payload.meta.is_dynasty
                                    ? `${side.ktc_equiv.toFixed(0)} KTC equiv`
                                    : `${side.ktc_equiv.toFixed(1)} VORP`}
                                </Text>
                              </Box>
                            ))}
                          </VStack>
                        </Td>
                        <Td isNumeric>
                          {trade.ktc_edge_per_season > 0
                            ? payload.meta.is_dynasty
                              ? `+${trade.ktc_edge_per_season.toFixed(0)}`
                              : `+${trade.ktc_edge_per_season.toFixed(1)}`
                            : '—'}
                        </Td>
                      </Tr>
                      {isOpen && (
                        <Tr bg="blue.50">
                          <Td colSpan={3} px={3} py={3}>
                            {payload.meta.is_dynasty ? (
                              <TradeInspector
                                leagueId={payload.meta.league_id}
                                transactionId={trade.transaction_id}
                                year={payload.meta.year}
                              />
                            ) : (
                              <TradeInspectorRedraft
                                leagueId={payload.meta.league_id}
                                transactionId={trade.transaction_id}
                                year={payload.meta.year}
                              />
                            )}
                          </Td>
                        </Tr>
                      )}
                    </React.Fragment>
                  );
                })}
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
    </VStack>
  );
};


/**
 * All-time hero accolade strip — eight crown cards keyed off the aggregator
 * payload's top-level fields (luckiest / unluckiest / worst_start_sit /
 * efficiency / trades). Each card collapses to "—" when the corresponding
 * accolade is null (e.g. a chain with no completed trades).
 */
const AllTimeAccolades: React.FC<{ data: WrappedAllTimeAccolades }> = ({ data }) => {
  const yearLabel = (n: number) => `${n} year${n === 1 ? '' : 's'}`;
  return (
    <SimpleGrid columns={{ base: 1, sm: 2, md: 4 }} gap={3}>
      {accoladeCard(
        '👑 All-time luckiest',
        data.luckiest?.username,
        data.luckiest ? `${yearLabel(data.luckiest.years_won)} crowned` : undefined,
      )}
      {accoladeCard(
        '💀 All-time unluckiest',
        data.unluckiest?.username,
        data.unluckiest ? `${yearLabel(data.unluckiest.years_won)} crowned` : undefined,
      )}
      {accoladeCard(
        '🤡 Worst start/sit',
        data.worst_start_sit?.username,
        data.worst_start_sit
          ? `+${data.worst_start_sit.total_troll_value.toFixed(1)} troll over ${yearLabel(data.worst_start_sit.years_counted)}`
          : undefined,
      )}
      {accoladeCard(
        '🧠 Best lineup-setter',
        data.most_efficient?.username,
        data.most_efficient
          ? `${data.most_efficient.avg_efficiency_pct.toFixed(1)}% avg over ${yearLabel(data.most_efficient.years_counted)}`
          : undefined,
      )}
      {accoladeCard(
        '🥱 Worst lineup-setter',
        data.least_efficient?.username,
        data.least_efficient
          ? `${data.least_efficient.avg_efficiency_pct.toFixed(1)}% avg over ${yearLabel(data.least_efficient.years_counted)}`
          : undefined,
      )}
      {accoladeCard(
        '🤝 Most active trader',
        data.most_active_trader?.username,
        data.most_active_trader
          ? `${data.most_active_trader.total_trades} total trade${data.most_active_trader.total_trades === 1 ? '' : 's'}`
          : undefined,
      )}
      {accoladeCard(
        '📈 Biggest net gainer',
        data.biggest_net_gainer?.username,
        data.biggest_net_gainer
          ? `+${data.biggest_net_gainer.net_value_gained.toFixed(0)} value`
          : undefined,
      )}
      {accoladeCard(
        '📉 Biggest net loser',
        data.biggest_net_loser?.username,
        data.biggest_net_loser
          ? `${data.biggest_net_loser.net_value_gained.toFixed(0)} value`
          : undefined,
      )}
    </SimpleGrid>
  );
};


/**
 * All-time view — hero accolade strip plus a Chakra accordion of every
 * season in the chain. Each accordion panel reuses ``YearSections``, so
 * adding a new per-season accolade automatically lights up here too.
 */
const AllTimeView: React.FC<{ data: WrappedAllTimeResponse }> = ({ data }) => {
  return (
    <VStack align="stretch" gap={4}>
      <Box bg="white" borderWidth={1} borderRadius="md" p={3}>
        <Heading size="sm" mb={2}>
          All-time accolades
        </Heading>
        <Text fontSize="xs" color="gray.500" mb={3}>
          Crowns and totals across every season this league has been on
          Sleeper. Players who've changed display names are aggregated by
          their stable Sleeper user_id and rendered with the most recent
          name we saw.
        </Text>
        <AllTimeAccolades data={data.all_time} />
      </Box>

      <Box bg="white" borderWidth={1} borderRadius="md" p={3}>
        <Heading size="sm" mb={2}>
          Season by season
        </Heading>
        <Text fontSize="xs" color="gray.500" mb={3}>
          {data.years.length} season{data.years.length === 1 ? '' : 's'} on
          record. Click a year to expand its full Wrapped.
        </Text>
        {data.years.length === 0 ? (
          <Text fontSize="sm" color="gray.500">
            No seasons could be loaded for this league.
          </Text>
        ) : (
          <Accordion allowToggle allowMultiple>
            {data.years.map((entry) => (
              <AccordionItem key={`${entry.year}-${entry.league_id}`}>
                <AccordionButton
                  _hover={{ bg: 'gray.50' }}
                  _expanded={{ bg: 'gray.100' }}
                >
                  <Box flex="1" textAlign="left" fontWeight="semibold" color="gray.800">
                    {entry.year}
                    {entry.payload.meta?.league_name && (
                      <Text as="span" fontWeight="normal" color="gray.500" ml={2}>
                        — {entry.payload.meta.league_name}
                      </Text>
                    )}
                  </Box>
                  <AccordionIcon color="gray.600" />
                </AccordionButton>
                <AccordionPanel pb={4} px={{ base: 0, md: 2 }}>
                  <YearSections payload={entry.payload} />
                </AccordionPanel>
              </AccordionItem>
            ))}
          </Accordion>
        )}
      </Box>
    </VStack>
  );
};


// Sentinel "year" the dropdown uses to request the all-time aggregator
// from the backend (``?year=all``).
const ALL_TIME_YEAR = 'all';


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
    const base =
      seasonChain && seasonChain.length > 0
        ? seasonChain.map((s) => s.season)
        : fallbackYears;
    // Always offer "All time" as the last option. Surface it only when
    // the chain has at least one season to walk — degenerate against a
    // league that returned a 404 chain.
    return [...base, ALL_TIME_YEAR];
  }, [seasonChain, fallbackYears]);
  // Year is sourced from the URL so the page is shareable and so navigating
  // to a new (resolved) league_id keeps the user on the year they picked.
  const urlYear = searchParams.get('year');
  const year =
    urlYear && yearOptions.includes(urlYear) ? urlYear : yearOptions[0];
  const [payload, setPayload] = useState<WrappedApiResponse | null>(null);
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
  //
  // For ``year=all`` we skip the resolve step entirely — the all-time
  // payload is keyed off the *current* league_id and the backend walks
  // the chain itself.
  const handleYearChange = async (newYear: string) => {
    if (!leagueId || newYear === year) return;
    if (newYear === ALL_TIME_YEAR) {
      navigate(
        `/wrapped/sleeper/${encodeURIComponent(leagueId)}?year=${ALL_TIME_YEAR}`,
      );
      return;
    }
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

  // Type guard — the all-time aggregator response is the only one with a
  // ``mode`` discriminator.
  const isAllTime = (
    p: WrappedApiResponse,
  ): p is WrappedAllTimeResponse =>
    (p as WrappedAllTimeResponse).mode === 'all_time';

  // Header label / dynasty tag come from the per-season meta; for all-time
  // we surface the most recent season's league_name (years are newest-first).
  const headerMeta = payload && !isAllTime(payload)
    ? payload.meta
    : payload && isAllTime(payload) && payload.years.length > 0
      ? payload.years[0].payload.meta
      : null;

  const yearLabel = (y: string) => (y === ALL_TIME_YEAR ? 'All time' : y);

  return (
    <Box p={{ base: 3, md: 6 }} maxW={{ base: '100%', xl: '1400px' }} mx="auto">
      <VStack align="stretch" gap={4}>
        <HStack justify="space-between" wrap="wrap" gap={3}>
          <Box>
            <Heading size="lg">League Wrapped</Heading>
            {headerMeta?.league_name && (
              <Text color="gray.600">
                {headerMeta.league_name}
                {headerMeta.is_dynasty && (
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
                  {yearLabel(y)}
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
          isAllTime(payload)
            ? <AllTimeView data={payload} />
            : <YearSections payload={payload} />
        )}
      </VStack>
    </Box>
  );
};

export default WrappedPage;

