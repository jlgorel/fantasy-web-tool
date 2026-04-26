import React, { useState, useEffect, useCallback } from "react";
import PlayerTable from "./PlayerTable";
import { useUUID } from "../context/UUIDContext";
import { VStack, HStack, Button, Text, Box, Spinner } from "@chakra-ui/react";
import { api } from "../api/client";
import { FreeAgentRecs, Player } from "../types/player";

interface DynamicTabsProps {
  showTabs: boolean;
}

const DynamicTabs: React.FC<DynamicTabsProps> = ({ showTabs }) => {
  const [leagueNames, setLeagueNames] = useState<string[]>([]);
  const [selectedTab, setSelectedTab] = useState<string | null>(null);
  const [suggestedStarts, setSuggestedStarts] = useState<Player[] | null>(null);
  const [freeAgentRecs, setFreeAgentRecs] = useState<FreeAgentRecs | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Stale-while-revalidate: when switching leagues, keep showing the
  // previous data so the page height stays stable. Just dim it + show a
  // small spinner so the user knows something is happening. This avoids
  // the layout jump where the username form bounces up under the tab
  // buttons during the half-second fetch.
  const [refreshing, setRefreshing] = useState(false);

  const userUUID = useUUID();

  const fetchLeagueData = useCallback(
    (leagueName: string, { keepStale = false }: { keepStale?: boolean } = {}) => {
      if (!userUUID) return;

      if (!keepStale) {
        setSuggestedStarts(null);
        setFreeAgentRecs(null);
      }
      setError(null);
      setRefreshing(true);

      api
        .loadLeagueData(userUUID, leagueName)
        .then((data) => {
          setSuggestedStarts(data.suggested_starts);
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
          // First load - no stale data exists yet, so use the default
          // (clear-then-fetch) path.
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
    // Keep showing the previous league's data while the new one loads;
    // prevents the layout collapse + username-form bounce.
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

      {selectedTab && suggestedStarts && (
        <Box position="relative" opacity={refreshing ? 0.55 : 1} transition="opacity 0.15s">
          <PlayerTable data={suggestedStarts} freeAgentRecs={freeAgentRecs ?? undefined} />
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

      {/* First-load fallback: no stale data yet, reserve some vertical space
          so the username form doesn't jump while initial fetch is in-flight. */}
      {selectedTab && !suggestedStarts && refreshing && (
        <VStack minH="40vh" justify="center">
          <Spinner size="xl" />
          <Text>Loading {selectedTab}...</Text>
        </VStack>
      )}
    </VStack>
  );
};

export default DynamicTabs;
