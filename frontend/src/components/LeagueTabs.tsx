import React, { useState, useEffect, useCallback } from "react";
import LineupResults from "./LineupResults";
import { useUUID } from "../context/UUIDContext";
import { VStack, HStack, Button, Text, Spinner } from "@chakra-ui/react";
import { api } from "../api/client";
import { FreeAgentRecs, Player } from "../types/player";

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

      {selectedTab && borisOptimized && (
        <LineupResults
          borisOptimized={borisOptimized}
          vegasOptimized={vegasOptimized}
          yourLineup={yourLineup}
          freeAgentRecs={freeAgentRecs ?? undefined}
          refreshing={refreshing}
          loadingLabel={selectedTab}
        />
      )}

      {selectedTab && !borisOptimized && refreshing && (
        <VStack minH="40vh" justify="center">
          <Spinner size="xl" />
          <Text>Loading {selectedTab}...</Text>
        </VStack>
      )}
    </VStack>
  );
};

export default DynamicTabs;
