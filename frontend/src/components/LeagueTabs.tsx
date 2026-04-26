import React, { useState, useEffect, useCallback } from "react";
import PlayerTable from "./PlayerTable";
import { useUUID } from "../context/UUIDContext";
import { VStack, HStack, Button, Text } from "@chakra-ui/react";
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

  const userUUID = useUUID();

  const fetchLeagueData = useCallback(
    (leagueName: string) => {
      if (!userUUID) return;

      setSuggestedStarts(null);
      setFreeAgentRecs(null);
      setError(null);

      api
        .loadLeagueData(userUUID, leagueName)
        .then((data) => {
          setSuggestedStarts(data.suggested_starts);
          setFreeAgentRecs(data.free_agent_recs);
        })
        .catch((err) => {
          console.error(err);
          setError("Failed to load league data.");
        });
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
    setSelectedTab(leagueName);
    fetchLeagueData(leagueName);
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
        <PlayerTable data={suggestedStarts} freeAgentRecs={freeAgentRecs ?? undefined} />
      )}
    </VStack>
  );
};

export default DynamicTabs;
