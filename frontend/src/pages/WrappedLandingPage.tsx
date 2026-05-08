/**
 * Landing page for /wrapped — three-step flow:
 *
 *   1. User enters their Sleeper username -> we fetch their current-year leagues.
 *   2. User picks a league from a dropdown.
 *   3. User picks the year to "wrap" -> we resolve the historical league_id
 *      (Sleeper assigns a new id per season for keeper/dynasty leagues) and
 *      navigate to /wrapped/sleeper/:resolvedId?year=YYYY so the URL is
 *      shareable and reflects the actual season being shown.
 *
 * No login required; everything we need is on Sleeper's public REST API.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Button,
  FormControl,
  FormHelperText,
  FormLabel,
  Heading,
  HStack,
  Input,
  Select,
  Spinner,
  Text,
  VStack,
} from '@chakra-ui/react';

import { api } from '../api/client';
import { SleeperLeagueSeason, SleeperLeagueSummary } from '../types/player';

function buildYearOptions(): string[] {
  // Match the WrappedPage selector: most recent fantasy year + 3 prior.
  const now = new Date();
  const fantasyYear =
    now.getMonth() < 2 ? now.getFullYear() - 1 : now.getFullYear();
  return [0, 1, 2, 3].map((i) => String(fantasyYear - i));
}

const WrappedLandingPage: React.FC = () => {
  const navigate = useNavigate();
  const yearOptions = useMemo(buildYearOptions, []);
  // The "lookup" year is the season we ask Sleeper to list leagues for.
  // Defaults to the current fantasy year, but the user can drop back —
  // useful when not every league has renewed for the upcoming season yet.
  // The "target" year is what season the user actually wants to wrap.
  const [lookupYear, setLookupYear] = useState(yearOptions[0]);

  const [username, setUsername] = useState('');
  const [leagues, setLeagues] = useState<SleeperLeagueSummary[] | null>(null);
  const [pickedLeagueId, setPickedLeagueId] = useState('');
  const [targetYear, setTargetYear] = useState(yearOptions[0]);
  const [loadingLeagues, setLoadingLeagues] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Season chain for the currently-picked league. Populates the "Wrap
  // which season?" dropdown with only the years that actually exist for
  // that league. Best-effort — falls back to the static yearOptions
  // window if the chain fetch fails.
  const [seasonChain, setSeasonChain] = useState<SleeperLeagueSeason[] | null>(
    null,
  );
  const targetYearOptions = useMemo(() => {
    if (seasonChain && seasonChain.length > 0) {
      return seasonChain.map((s) => s.season);
    }
    return yearOptions;
  }, [seasonChain, yearOptions]);

  // Whenever the user picks a different league, walk that league's
  // previous_league_id chain to learn which seasons it actually has.
  useEffect(() => {
    if (!pickedLeagueId) {
      setSeasonChain(null);
      return;
    }
    let cancelled = false;
    api
      .getSleeperLeagueSeasons(pickedLeagueId)
      .then((resp) => {
        if (cancelled) return;
        setSeasonChain(resp.seasons);
        // If the current targetYear isn't in the new chain, snap to the
        // most recent season this league has.
        if (resp.seasons.length > 0) {
          const seasons = resp.seasons.map((s) => s.season);
          if (!seasons.includes(targetYear)) {
            setTargetYear(seasons[0]);
          }
        }
      })
      .catch((err) => {
        console.warn('getSleeperLeagueSeasons failed; using fallback', err);
      });
    return () => {
      cancelled = true;
    };
    // targetYear deliberately omitted — we only want to refetch when the
    // league actually changes; the snap-to-most-recent inside is enough.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickedLeagueId]);

  const fetchLeagues = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = username.trim();
    if (!trimmed) return;
    setLoadingLeagues(true);
    setError(null);
    setLeagues(null);
    setPickedLeagueId('');
    try {
      const resp = await api.getSleeperUserLeagues(trimmed, lookupYear);
      if (!resp.leagues.length) {
        setError(
          `No ${lookupYear} Sleeper leagues found for "${trimmed}". ` +
            `Try a different year, or double-check the username ` +
            `(it's case-sensitive on Sleeper).`,
        );
      } else {
        setLeagues(resp.leagues);
        setPickedLeagueId(resp.leagues[0].league_id);
        // Default the target year to whatever year we just looked up —
        // most users want to wrap the season they searched for.
        setTargetYear(lookupYear);
      }
    } catch (err) {
      console.error('getSleeperUserLeagues failed', err);
      setError(`Could not look up leagues for "${trimmed}".`);
    } finally {
      setLoadingLeagues(false);
    }
  };

  const goToWrapped = async () => {
    if (!pickedLeagueId) return;
    setResolving(true);
    setError(null);
    try {
      // Fast path: if the target year matches the lookup year, the picked
      // league_id is already the right one — skip the resolve hop.
      if (targetYear === lookupYear) {
        navigate(
          `/wrapped/sleeper/${encodeURIComponent(pickedLeagueId)}?year=${encodeURIComponent(targetYear)}`,
        );
        return;
      }
      const resp = await api.resolveSleeperLeague(pickedLeagueId, targetYear);
      if (resp.league_id) {
        navigate(
          `/wrapped/sleeper/${encodeURIComponent(resp.league_id)}?year=${encodeURIComponent(targetYear)}`,
        );
      } else {
        setError(
          `This league doesn't have a ${targetYear} season on Sleeper.`,
        );
      }
    } catch (err) {
      console.error('resolveSleeperLeague failed', err);
      setError(`Could not resolve a ${targetYear} league_id for this league.`);
    } finally {
      setResolving(false);
    }
  };

  return (
    <Box p={{ base: 4, md: 8 }} maxW="640px" mx="auto">
      <VStack align="stretch" gap={4}>
        <Heading size="lg">League Wrapped</Heading>
        <Text color="gray.600">
          Generate a season-recap dashboard for any Sleeper league. We'll show
          luck, consistency, manager efficiency, hypothetical schedules, and
          weekly scoring trends — no login required.
        </Text>

        {/* Step 1: pick lookup year + username */}
        <form onSubmit={fetchLeagues}>
          <VStack align="stretch" gap={3}>
            <FormControl maxW="160px">
              <FormLabel>Season to search</FormLabel>
              <Select
                value={lookupYear}
                onChange={(e) => setLookupYear(e.target.value)}
                isDisabled={loadingLeagues}
              >
                {yearOptions.map((y) => (
                  <option key={y} value={y}>
                    {y}
                  </option>
                ))}
              </Select>
            </FormControl>
            <FormControl>
              <FormLabel>Sleeper username</FormLabel>
              <HStack>
                <Input
                  placeholder="e.g. jlgorel"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  isDisabled={loadingLeagues}
                />
                <Button
                  type="submit"
                  colorScheme="blue"
                  isLoading={loadingLeagues}
                  isDisabled={!username.trim()}
                >
                  Find leagues
                </Button>
              </HStack>
              <FormHelperText>
                We look up every league you were in for the selected season.
                If your league hasn't renewed for next year yet, drop the
                season back. Nothing is stored.
              </FormHelperText>
            </FormControl>
          </VStack>
        </form>

        {loadingLeagues && (
          <Box py={4} textAlign="center">
            <Spinner />
          </Box>
        )}

        {/* Step 2 + 3: pick league, pick year, go */}
        {leagues && leagues.length > 0 && (
          <VStack align="stretch" gap={3}>
            <FormControl>
              <FormLabel>League ({lookupYear})</FormLabel>
              <Select
                value={pickedLeagueId}
                onChange={(e) => setPickedLeagueId(e.target.value)}
              >
                {leagues.map((lg) => (
                  <option key={lg.league_id} value={lg.league_id}>
                    {lg.name ?? lg.league_id}
                    {lg.total_rosters ? ` (${lg.total_rosters} teams)` : ''}
                  </option>
                ))}
              </Select>
              <FormHelperText>
                When you pick a different season below, we'll automatically
                walk Sleeper's previous-season chain to find the right
                league_id for that year.
              </FormHelperText>
            </FormControl>

            <FormControl>
              <FormLabel>Wrap which season?</FormLabel>
              <Select
                value={targetYear}
                onChange={(e) => setTargetYear(e.target.value)}
              >
                {targetYearOptions.map((y) => (
                  <option key={y} value={y}>
                    {y}
                  </option>
                ))}
              </Select>
              {seasonChain && seasonChain.length > 0 && (
                <FormHelperText>
                  Showing the {seasonChain.length} season
                  {seasonChain.length === 1 ? '' : 's'} this league has on
                  Sleeper.
                </FormHelperText>
              )}
            </FormControl>

            <Button
              colorScheme="blue"
              onClick={goToWrapped}
              isLoading={resolving}
              isDisabled={!pickedLeagueId}
            >
              Open Wrapped
            </Button>
          </VStack>
        )}

        {error && (
          <Box
            bg="red.50"
            borderWidth={1}
            borderColor="red.200"
            borderRadius="md"
            p={3}
          >
            <Text color="red.700" fontSize="sm">
              {error}
            </Text>
          </Box>
        )}
      </VStack>
    </Box>
  );
};

export default WrappedLandingPage;
