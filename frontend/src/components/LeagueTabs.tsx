import React, { useState, useEffect, useCallback, useMemo } from "react";
import PlayerTable from "./PlayerTable";
import LineupConfidence from "./LineupConfidence";
import { useUUID } from "../context/UUIDContext";
import { VStack, HStack, Button, Text, Box, Spinner, ButtonGroup } from "@chakra-ui/react";
import { api } from "../api/client";
import { FreeAgentRecs, Player } from "../types/player";

type LineupView = "boris" | "vegas" | "your";

interface DynamicTabsProps {
  showTabs: boolean;
}

const DynamicTabs: React.FC<DynamicTabsProps> = ({ showTabs }) => {
  const [leagueNames, setLeagueNames] = useState<string[]>([]);
  const [selectedTab, setSelectedTab] = useState<string | null>(null);
  const [borisOptimized, setBorisOptimized] = useState<Player[] | null>(null);
  const [vegasOptimized, setVegasOptimized] = useState<Player[] | null>(null);
  const [yourLineup, setYourLineup] = useState<Player[] | null>(null);
  const [freeAgentRecs, setFreeAgentRecs] = useState<FreeAgentRecs | null>(null);
  const [lineupView, setLineupView] = useState<LineupView>("boris");
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const userUUID = useUUID();

  const fetchLeagueData = useCallback(
    (leagueName: string, { keepStale = false }: { keepStale?: boolean } = {}) => {
      if (!userUUID) return;

      if (!keepStale) {
        setBorisOptimized(null);
        setVegasOptimized(null);
        setYourLineup(null);
        setFreeAgentRecs(null);
      }
      setError(null);
      setRefreshing(true);

      api
        .loadLeagueData(userUUID, leagueName)
        .then((data) => {
          // Prefer the new explicit field; fall back to the legacy
          // `suggested_starts` alias for older backend responses.
          setBorisOptimized(data.boris_optimized ?? data.suggested_starts);
          setVegasOptimized(data.vegas_optimized ?? null);
          setYourLineup(data.your_lineup ?? null);
          setFreeAgentRecs(data.free_agent_recs);
        })
        .catch((err) => {
          console.error(err);
          setError("Failed to load league data.");
        })
        .finally(() => setRefreshing(false));
    },
    [userUUID]
  );

  useEffect(() => {
    if (!showTabs || !userUUID) return;

    api
      .loadCachedStarts(userUUID)
      .then((data) => {
        const names = data.league_names ?? [];
        setLeagueNames(names);
        if (names.length > 0) {
          setSelectedTab(names[0]);
          fetchLeagueData(names[0]);
        }
      })
      .catch((err) => {
        console.error(err);
        setError("Failed to load league names.");
      });
  }, [showTabs, userUUID, fetchLeagueData]);

  const handleTabChange = (leagueName: string) => {
    if (leagueName === selectedTab) return;
    setSelectedTab(leagueName);
    fetchLeagueData(leagueName, { keepStale: true });
  };

  // Pick which lineup to render. If the user picked "your" but this league
  // has no user-set starters available, fall back to the boris optimizer.
  const displayedLineup = useMemo<Player[] | null>(() => {
    if (lineupView === "vegas") return vegasOptimized ?? borisOptimized;
    if (lineupView === "your") return yourLineup ?? borisOptimized;
    return borisOptimized;
  }, [lineupView, borisOptimized, vegasOptimized, yourLineup]);

  const hasYourLineup = !!yourLineup;
  const hasVegasLineup = !!vegasOptimized;

  if (!showTabs) return null;

  return (
    <VStack align="stretch" gap={4} mt={4}>
      <HStack gap={2} wrap="wrap" justify="center">
        {leagueNames.map((name) => (
          <Button
            key={name}
            onClick={() => handleTabChange(name)}
            colorScheme={selectedTab === name ? "blue" : "gray"}
            isDisabled={refreshing && selectedTab !== name}
          >
            {name}
          </Button>
        ))}
      </HStack>

      {error && (
        <Text color="red.500" alignSelf="center">
          {error}
        </Text>
      )}

      {selectedTab && displayedLineup && (
        <Box position="relative" opacity={refreshing ? 0.55 : 1} transition="opacity 0.15s">
          {/* Lineup view selector. Hide entirely if there's no comparison
              available (no vegas + no your-lineup) so off-season / Fleaflicker
              users don't see a useless 1-button toggle. */}
          {(hasYourLineup || hasVegasLineup) && (
            <HStack justify="center" mb={2}>
              <ButtonGroup size="sm" isAttached variant="outline">
                <Button
                  onClick={() => setLineupView("boris")}
                  colorScheme={lineupView === "boris" ? "blue" : "gray"}
                  variant={lineupView === "boris" ? "solid" : "outline"}
                >
                  Boris-Optimized
                </Button>
                {hasVegasLineup && (
                  <Button
                    onClick={() => setLineupView("vegas")}
                    colorScheme={lineupView === "vegas" ? "blue" : "gray"}
                    variant={lineupView === "vegas" ? "solid" : "outline"}
                  >
                    Vegas-Optimized
                  </Button>
                )}
                {hasYourLineup && (
                  <Button
                    onClick={() => setLineupView("your")}
                    colorScheme={lineupView === "your" ? "blue" : "gray"}
                    variant={lineupView === "your" ? "solid" : "outline"}
                  >
                    Your Lineup
                  </Button>
                )}
              </ButtonGroup>
            </HStack>
          )}

          <LineupConfidence starters={displayedLineup} />
          <PlayerTable data={displayedLineup} freeAgentRecs={freeAgentRecs ?? undefined} />
          {refreshing && (
            <Box
              position="absolute"
              top={4}
              right={4}
              bg="white"
              borderWidth="1px"
              borderRadius="md"
              px={3}
              py={2}
              boxShadow="md"
              zIndex={2}
            >
              <HStack gap={2}>
                <Spinner size="sm" />
                <Text fontSize="sm">Loading {selectedTab}...</Text>
              </HStack>
            </Box>
          )}
        </Box>
      )}

      {selectedTab && !displayedLineup && refreshing && (
        <VStack minH="40vh" justify="center">
          <Spinner size="xl" />
          <Text>Loading {selectedTab}...</Text>
        </VStack>
      )}
    </VStack>
  );
};

export default DynamicTabs;
