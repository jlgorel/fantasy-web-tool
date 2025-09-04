import React, { useState, useEffect, useCallback } from 'react';
import PlayerTable from './PlayerTable';
import { useUUID } from '../context/UUIDContext';
import { VStack, HStack, Button, Text } from '@chakra-ui/react';

if (!process.env.REACT_APP_API_BASE_URL) {
  throw new Error("REACT_APP_API_BASE_URL is not set!");
}
export const API_BASE = process.env.REACT_APP_API_BASE_URL;

interface DataDictionary {
  [key: string]: any;
}

interface Player {
  NAME: string;
  POS: string;
  POS_RANK: string;
  FLEX?: string;
}

interface DynamicTabsProps {
  showTabs: boolean;
}

const DynamicTabs: React.FC<DynamicTabsProps> = ({ showTabs }) => {
  const [data, setData] = useState<DataDictionary>({});
  const [selectedTab, setSelectedTab] = useState<string | null>(null);
  const [leagueData, setLeagueData] = useState<Player[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const userUUID = useUUID();

  // Fetch league data for selected tab
  const fetchLeagueData = useCallback(
    (leagueName: string) => {
      if (!userUUID) return;

      setLeagueData(null);
      setError(null);

      fetch(`${API_BASE}/load-league-data?league=${encodeURIComponent(leagueName)}`, {
        headers: { 'X-User-UUID': userUUID },
      })
        .then(res => {
          if (!res.ok) throw new Error('Network response not ok');
          return res.json();
        })
        .then((data: Player[]) => {
          setLeagueData(data);
        })
        .catch(err => {
          console.error(err);
          setError('Failed to load league data.');
        });
    },
    [userUUID]
  );

  useEffect(() => {
    if (!showTabs) return;

    if (userUUID) {
      fetch(`${API_BASE}/load-cached-starts`, {
        headers: { 'X-User-UUID': userUUID },
      })
        .then(res => res.json())
        .then((data: DataDictionary) => {
          const leagueNames = data['league_names'];
          setData(leagueNames);

          const firstKey = Object.keys(leagueNames)[0];
          setSelectedTab(leagueNames[firstKey]);
        })
        .catch(err => {
          console.error(err);
          setError('Failed to load league names.');
        });
    }
  }, [showTabs, userUUID]);

  const handleTabChange = (leagueName: string) => {
    setSelectedTab(leagueName);
    fetchLeagueData(leagueName);
  };

  if (!showTabs) return null;

  return (
    <VStack align="stretch" gap={4} mt={4}>
      <HStack gap={2} wrap="wrap">
        {Object.keys(data).map(key => (
          <Button
            key={data[key]}
            onClick={() => handleTabChange(data[key])}
            colorScheme={selectedTab === data[key] ? 'blue' : 'gray'}
          >
            {data[key]}
          </Button>
        ))}
      </HStack>

      {error && (
        <Text color="red.500" alignSelf="center">
          {error}
        </Text>
      )}

      {selectedTab && leagueData && <PlayerTable data={leagueData} />}
    </VStack>
  );
};

export default DynamicTabs;
