/**
 * Draft Help tab. Three habit summaries, all driven off the public Sleeper API:
 *
 *   1. "Your Habits"   -> your tendencies across all your leagues (username only)
 *   2. "This League"   -> every manager's tendencies in a selected league
 *   3. "Opponents"     -> your league-mates' tendencies in their OTHER leagues
 *                         (crawls extra leagues -> opt-in + slow warning)
 *
 * Enter a Sleeper username to load your leagues, pick one, then open a tab.
 * The Monte-Carlo live draft helper is a separate, later phase.
 */
import React, { useState } from 'react';
import {
  Alert,
  AlertIcon,
  Badge,
  Box,
  Button,
  Heading,
  HStack,
  Input,
  Select,
  SimpleGrid,
  Spinner,
  Table,
  Tab,
  TabList,
  TabPanel,
  TabPanels,
  Tabs,
  Tag,
  Tbody,
  Td,
  Text,
  Th,
  Thead,
  Tr,
  VStack,
  Wrap,
  WrapItem,
} from '@chakra-ui/react';

import { api } from '../api/client';
import MockDraftView from '../components/MockDraftView';
import { SleeperLeagueSummary } from '../types/player';
import {
  AuctionSummary,
  AggregatedMarketStatus,
  LeagueHabitsResponse,
  ManagerSummary,
  OpponentsHabitsResponse,
  SnakeSummary,
  UserHabitsResponse,
  LeagueWide,
} from '../types/draft';

const ARCHETYPE_LABELS: Record<string, string> = {
  zero_rb: 'Zero-RB',
  hero_rb: 'Hero-RB',
  rb_heavy: 'RB-heavy',
  balanced: 'Balanced',
  unknown: 'Unknown',
};

function fmtSigned(n: number | null | undefined): string {
  if (n == null) return '–';
  return (n > 0 ? '+' : '') + n.toFixed(1);
}

// ---------------------------------------------------------------------------
// Small render helpers
// ---------------------------------------------------------------------------
const PositionCounts: React.FC<{ counts: Record<string, number> }> = ({ counts }) => {
  const entries = Object.entries(counts || {}).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return <Text color="gray.500">—</Text>;
  return (
    <Wrap>
      {entries.map(([pos, n]) => (
        <WrapItem key={pos}>
          <Tag colorScheme="blue" variant="subtle">{pos}: {n}</Tag>
        </WrapItem>
      ))}
    </Wrap>
  );
};

const PositionByRoundTable: React.FC<{ pbr: Record<string, Record<string, number>> }> = ({ pbr }) => {
  const rounds = Object.keys(pbr || {}).sort((a, b) => Number(a) - Number(b));
  const positions = Array.from(
    new Set(rounds.flatMap((r) => Object.keys(pbr[r]))),
  ).sort();
  if (rounds.length === 0) return <Text color="gray.500">No snake picks.</Text>;
  return (
    <Box overflowX="auto">
      <Table size="sm" variant="simple">
        <Thead>
          <Tr>
            <Th>Round</Th>
            {positions.map((p) => <Th key={p} isNumeric>{p}</Th>)}
          </Tr>
        </Thead>
        <Tbody>
          {rounds.map((r) => (
            <Tr key={r}>
              <Td fontWeight="bold">{r}</Td>
              {positions.map((p) => (
                <Td key={p} isNumeric>{pbr[r][p] || ''}</Td>
              ))}
            </Tr>
          ))}
        </Tbody>
      </Table>
    </Box>
  );
};

const SnakeView: React.FC<{ s: SnakeSummary }> = ({ s }) => (
  <VStack align="stretch" spacing={3}>
    <Box>
      <Text fontWeight="semibold" mb={1}>Early-round archetype ({s.drafts_counted} draft(s))</Text>
      <Wrap>
        {Object.entries(s.archetypes || {}).map(([a, n]) => (
          <WrapItem key={a}>
            <Tag colorScheme="purple">{ARCHETYPE_LABELS[a] || a}: {n}</Tag>
          </WrapItem>
        ))}
      </Wrap>
    </Box>
    <Box>
      <Text fontWeight="semibold" mb={1}>First 3 rounds, by position</Text>
      <PositionCounts counts={s.early_round_mix} />
    </Box>
    {s.reach && s.reach.picks_evaluated > 0 && (
      <Box>
        <Text fontWeight="semibold" mb={1}>
          Reach vs. steal{' '}
          <Badge colorScheme={(s.reach.avg_vbd_delta || 0) >= 0 ? 'green' : 'red'}>
            avg {fmtSigned(s.reach.avg_vbd_delta)} VBD
          </Badge>
        </Text>
        <Text fontSize="sm" color="gray.600">
          VBD points gained (+, a steal) or left on the board (−, a reach) vs. the
          player available at each slot. Only early picks (through pick 70) are
          scored — the value curve is flat after that.
        </Text>
        <HStack mt={1} spacing={6} align="start" flexWrap="wrap">
          {s.reach.biggest_value && (
            <Text fontSize="sm">
              Best steal: <b>{s.reach.biggest_value.name}</b> (pick {s.reach.biggest_value.pick_no}
              {s.reach.biggest_value.expected_overall_rank ? `, rank #${s.reach.biggest_value.expected_overall_rank}` : ''},
              {' '}{fmtSigned(s.reach.biggest_value.vbd_delta)} VBD)
            </Text>
          )}
          {s.reach.biggest_reach && (
            <Text fontSize="sm">
              Biggest reach: <b>{s.reach.biggest_reach.name}</b> (pick {s.reach.biggest_reach.pick_no}
              {s.reach.biggest_reach.expected_overall_rank ? `, rank #${s.reach.biggest_reach.expected_overall_rank}` : ''},
              {' '}{fmtSigned(s.reach.biggest_reach.vbd_delta)} VBD)
            </Text>
          )}
        </HStack>
      </Box>
    )}
    <Box>
      <Text fontWeight="semibold" mb={1}>Position by round</Text>
      <PositionByRoundTable pbr={s.position_by_round} />
    </Box>
  </VStack>
);

const AuctionView: React.FC<{ a: AuctionSummary }> = ({ a }) => {
  const spend = a.avg_spend_by_position || {};
  const total = Object.values(spend).reduce((x, y) => x + y, 0) || 1;
  const positions = Object.keys(spend).sort((p, q) => spend[q] - spend[p]);
  return (
    <VStack align="stretch" spacing={3}>
      <HStack spacing={6} flexWrap="wrap">
        <Badge colorScheme="orange">
          Stars &amp; scrubs index: {(a.avg_stars_and_scrubs_index * 100).toFixed(0)}%
        </Badge>
        <Badge colorScheme="orange">
          Top buy: {(a.avg_max_bid_pct_budget * 100).toFixed(0)}% of budget
        </Badge>
        <Text fontSize="sm" color="gray.600">{a.drafts_counted} auction(s)</Text>
      </HStack>
      <Box>
        <Text fontWeight="semibold" mb={1}>Average spend by position</Text>
        <Box overflowX="auto">
          <Table size="sm" variant="simple">
            <Thead>
              <Tr><Th>Pos</Th><Th isNumeric>Avg $</Th><Th isNumeric>Share</Th></Tr>
            </Thead>
            <Tbody>
              {positions.map((p) => (
                <Tr key={p}>
                  <Td fontWeight="bold">{p}</Td>
                  <Td isNumeric>${spend[p].toFixed(0)}</Td>
                  <Td isNumeric>{((spend[p] / total) * 100).toFixed(0)}%</Td>
                </Tr>
              ))}
            </Tbody>
          </Table>
        </Box>
      </Box>
    </VStack>
  );
};

const FavoritesView: React.FC<{ m: ManagerSummary }> = ({ m }) => {
  if (!m.favorites || m.favorites.length === 0) return null;
  return (
    <Box>
      <Text fontWeight="semibold" mb={1}>Repeat targets</Text>
      <Wrap>
        {m.favorites.map((f) => (
          <WrapItem key={f.player_id}>
            <Tag colorScheme="teal">{f.name} ×{f.count}</Tag>
          </WrapItem>
        ))}
      </Wrap>
    </Box>
  );
};

const ManagerSummaryView: React.FC<{ m: ManagerSummary }> = ({ m }) => {
  if (!m || (!m.snake && !m.auction && !m.favorites)) {
    return <Text color="gray.500">No draft data found.</Text>;
  }
  return (
    <VStack align="stretch" spacing={4}>
      {m.snake && <SnakeView s={m.snake} />}
      {m.auction && <AuctionView a={m.auction} />}
      <FavoritesView m={m} />
    </VStack>
  );
};

const ELITE_PATTERN_LABEL: Record<string, string> = {
  hot_start: 'starts HOT — top players are overpaid early',
  cold_start: 'starts COLD — top players are steals early',
  flat: 'is fairly flat — bids track value',
};
const ELITE_PATTERN_ADVICE: Record<string, string> = {
  hot_start: 'Let the room overpay early, then pounce on stud steals later.',
  cold_start: 'Grab a stud while bidding is shy early, then wait for value.',
  flat: 'No strong early/late tilt — just bid to value.',
};
const MARKET_ORDER = ['WR', 'RB', 'TE', 'QB'];

const MarketStatusLine: React.FC<{ pos: string; m: AggregatedMarketStatus }> = ({ pos, m }) => {
  const emphasize = pos === 'WR';
  if (!m || m.drafts_analyzed === 0) {
    return (
      <Text fontSize="sm" color="gray.500">
        <b>{pos}</b>: not enough auction data.
      </Text>
    );
  }
  if (m.crashed && m.latest) {
    return (
      <Text
        fontSize="sm"
        fontWeight={emphasize ? 'semibold' : 'normal'}
        color={emphasize ? 'red.600' : undefined}
      >
        <b>{pos}</b> market crashes (in {m.crashed_in}/{m.drafts_analyzed} auction(s)) — latest:
        cools after {m.latest.crash_after} bought (pick {m.latest.crash_pick_no}),{' '}
        {fmtSigned(m.latest.avg_inflation_before)}% → {fmtSigned(m.latest.avg_inflation_after)}% vs. value.
        {emphasize ? ' Wait on WRs.' : ''}
      </Text>
    );
  }
  return (
    <Text fontSize="sm" color="gray.600">
      <b>{pos}</b> market holds firm (no crash across {m.drafts_analyzed} auction(s)).
    </Text>
  );
};

const AuctionMarketView: React.FC<{ lw: LeagueWide }> = ({ lw }) => {
  const mc = lw.market_crash || {};
  const positions = MARKET_ORDER.filter((p) => p in mc).concat(
    Object.keys(mc).filter((p) => !MARKET_ORDER.includes(p)),
  );
  return (
    <VStack align="stretch" spacing={2}>
      {lw.elite_market && (
        <Box>
          <Text fontSize="sm" fontWeight="semibold">
            Elite market {ELITE_PATTERN_LABEL[lw.elite_market.pattern] || lw.elite_market.pattern}
          </Text>
          <Text fontSize="sm" color="gray.600">
            Top players go {fmtSigned(lw.elite_market.early_inflation)}% vs. value early →{' '}
            {fmtSigned(lw.elite_market.late_inflation)}% late (across{' '}
            {lw.elite_market.drafts_analyzed} auction(s)).{' '}
            {ELITE_PATTERN_ADVICE[lw.elite_market.pattern] || ''}
          </Text>
        </Box>
      )}
      {positions.length > 0 && (
        <VStack align="stretch" spacing={1}>
          {positions.map((pos) => (
            <MarketStatusLine key={pos} pos={pos} m={mc[pos]} />
          ))}
        </VStack>
      )}
    </VStack>
  );
};

const LeagueWideView: React.FC<{ lw: LeagueWide }> = ({ lw }) => {
  if (!lw || !lw.draft_type) return null;
  return (
    <Box borderWidth="1px" borderRadius="md" p={3} bg="gray.50">
      <Heading size="sm" mb={2}>League-wide patterns ({lw.draft_type})</Heading>
      {lw.draft_type === 'snake' && lw.runs && (
        <VStack align="stretch" spacing={1}>
          {Object.entries(lw.runs).map(([pos, runs]) => (
            <Text key={pos} fontSize="sm">
              <b>{pos}</b> runs: {runs.map((r) => `${r.count} between picks ${r.start_pick}-${r.end_pick}`).join('; ')}
            </Text>
          ))}
          {lw.first_five_off_board && (
            <Text fontSize="sm" color="gray.600">
              First off the board — {Object.entries(lw.first_five_off_board)
                .map(([pos, picks]) => `${pos}: ${picks.join(', ')}`).join(' | ')}
            </Text>
          )}
        </VStack>
      )}
      {lw.draft_type === 'auction' && <AuctionMarketView lw={lw} />}
    </Box>
  );
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
const DraftHelpPage: React.FC = () => {
  const [username, setUsername] = useState('');
  const [leagues, setLeagues] = useState<SleeperLeagueSummary[] | null>(null);
  const [leagueId, setLeagueId] = useState('');
  const [loadingLeagues, setLoadingLeagues] = useState(false);
  const [topError, setTopError] = useState<string | null>(null);

  // Per-tab state.
  const [userData, setUserData] = useState<UserHabitsResponse | null>(null);
  const [userLoading, setUserLoading] = useState(false);
  const [leagueData, setLeagueData] = useState<LeagueHabitsResponse | null>(null);
  const [leagueLoading, setLeagueLoading] = useState(false);
  const [oppData, setOppData] = useState<OpponentsHabitsResponse | null>(null);
  const [oppLoading, setOppLoading] = useState(false);
  const [tabError, setTabError] = useState<string | null>(null);

  const findLeagues = async () => {
    if (!username.trim()) return;
    setLoadingLeagues(true);
    setTopError(null);
    setLeagues(null);
    try {
      // Draft Help is redraft/keeper only -- never list dynasty leagues.
      const resp = await api.getSleeperUserLeagues(username.trim(), undefined, true);
      setLeagues(resp.leagues);
      if (resp.leagues.length > 0) setLeagueId(resp.leagues[0].league_id);
    } catch (e) {
      setTopError('Could not load leagues for that username.');
    } finally {
      setLoadingLeagues(false);
    }
  };

  const runUser = async () => {
    if (!username.trim()) return;
    setUserLoading(true);
    setTabError(null);
    try {
      setUserData(await api.getDraftHelpUserHabits(username.trim()));
    } catch (e) {
      setTabError('Failed to analyze your habits.');
    } finally {
      setUserLoading(false);
    }
  };

  const runLeague = async () => {
    if (!leagueId) return;
    setLeagueLoading(true);
    setTabError(null);
    try {
      setLeagueData(await api.getDraftHelpLeagueHabits(leagueId));
    } catch (e) {
      setTabError('Failed to analyze the league.');
    } finally {
      setLeagueLoading(false);
    }
  };

  const runOpponents = async () => {
    if (!leagueId) return;
    setOppLoading(true);
    setTabError(null);
    try {
      setOppData(await api.getDraftHelpOpponents(leagueId));
    } catch (e) {
      setTabError('Failed to crawl opponents.');
    } finally {
      setOppLoading(false);
    }
  };

  return (
    <Box maxW="1000px" mx="auto" px={{ base: 3, md: 6 }} py={6}>
      <Heading size="lg" mb={1}>Draft Help</Heading>
      <Text color="gray.600" mb={4}>
        Summarize draft habits for snake &amp; auction leagues. Enter your Sleeper username to begin.
      </Text>

      <VStack align="stretch" spacing={3} mb={6}>
        <HStack>
          <Input
            placeholder="Sleeper username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') findLeagues(); }}
            maxW="320px"
          />
          <Button onClick={findLeagues} colorScheme="blue" isLoading={loadingLeagues}>
            Find leagues
          </Button>
        </HStack>
        {topError && (
          <Alert status="error" borderRadius="md"><AlertIcon />{topError}</Alert>
        )}
        {leagues && (
          <HStack>
            <Text fontSize="sm" color="gray.600">League:</Text>
            <Select maxW="420px" value={leagueId} onChange={(e) => setLeagueId(e.target.value)}>
              {leagues.map((lg) => (
                <option key={lg.league_id} value={lg.league_id}>
                  {lg.name || lg.league_id} ({lg.season})
                </option>
              ))}
            </Select>
          </HStack>
        )}
      </VStack>

      {tabError && (
        <Alert status="error" borderRadius="md" mb={3}><AlertIcon />{tabError}</Alert>
      )}

      <Tabs colorScheme="blue" variant="enclosed">
        <TabList>
          <Tab>Your Habits</Tab>
          <Tab>This League</Tab>
          <Tab>Opponents</Tab>
          <Tab>Mock Draft</Tab>
        </TabList>
        <TabPanels>
          {/* Your habits */}
          <TabPanel px={0}>
            <Button onClick={runUser} colorScheme="blue" isLoading={userLoading}
              isDisabled={!username.trim()} mb={3}>
              Analyze my habits across all leagues
            </Button>
            {userData?.error && (
              <Alert status="warning" borderRadius="md"><AlertIcon />User not found.</Alert>
            )}
            {userData && !userData.error && (
              <Box>
                <Text fontSize="sm" color="gray.600" mb={2}>
                  Scanned {userData.leagues_scanned ?? 0} league(s) with drafts.
                </Text>
                <ManagerSummaryView m={userData.summary || {}} />
              </Box>
            )}
          </TabPanel>

          {/* This league */}
          <TabPanel px={0}>
            <Button onClick={runLeague} colorScheme="blue" isLoading={leagueLoading}
              isDisabled={!leagueId} mb={3}>
              Analyze this league
            </Button>
            {leagueData && (
              <VStack align="stretch" spacing={4}>
                <LeagueWideView lw={leagueData.league_wide} />
                <SimpleGrid columns={{ base: 1, md: 2 }} spacing={4}>
                  {Object.entries(leagueData.managers).map(([uid, m]) => (
                    <Box key={uid} borderWidth="1px" borderRadius="md" p={3}>
                      <Heading size="sm" mb={2}>{m.username}</Heading>
                      <ManagerSummaryView m={m} />
                    </Box>
                  ))}
                </SimpleGrid>
              </VStack>
            )}
          </TabPanel>

          {/* Opponents */}
          <TabPanel px={0}>
            <Alert status="info" borderRadius="md" mb={3}>
              <AlertIcon />
              This crawls each league-mate&apos;s other leagues and can be slow (capped at 5
              leagues × 3 seasons per opponent).
            </Alert>
            <Button onClick={runOpponents} colorScheme="orange" isLoading={oppLoading}
              isDisabled={!leagueId} mb={3}>
              Crawl opponents (slow)
            </Button>
            {oppLoading && <HStack><Spinner size="sm" /><Text>Crawling…</Text></HStack>}
            {oppData && (
              <SimpleGrid columns={{ base: 1, md: 2 }} spacing={4}>
                {Object.entries(oppData.opponents).map(([uid, opp]) => (
                  <Box key={uid} borderWidth="1px" borderRadius="md" p={3}>
                    <Heading size="sm" mb={1}>{opp.username}</Heading>
                    <Text fontSize="xs" color="gray.500" mb={2}>
                      {opp.leagues_scanned} other league(s) scanned
                    </Text>
                    <ManagerSummaryView m={opp.summary} />
                  </Box>
                ))}
              </SimpleGrid>
            )}
          </TabPanel>

          {/* Mock draft + Monte-Carlo recommender */}
          <TabPanel px={0}>
            <MockDraftView />
          </TabPanel>
        </TabPanels>
      </Tabs>
    </Box>
  );
};

export default DraftHelpPage;
