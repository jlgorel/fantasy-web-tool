import React, { useState, useEffect, useCallback } from 'react';
import PlayerTable from './PlayerTable';
import { useUUID } from '../context/UUIDContext';

interface Player {
  NAME: string;
  POS: string;
  POS_RANK: string;
  FLEX?: string;
}

interface FreeAgent {
  NAME: string;
  POS: string;
  VEGAS: string;
}

interface DataDictionary {
  [key: string]: any;
}

interface DynamicTabsProps {
  showTabs: boolean;
}

const DynamicTabs: React.FC<DynamicTabsProps> = ({ showTabs }) => {
  const [data, setData] = useState<DataDictionary>({});
  const [selectedTab, setSelectedTab] = useState<string | null>(null);
  const [leagueData, setLeagueData] = useState<Player[] | null>(null);
  const [freeAgentData, setFreeAgentData] = useState<FreeAgent[] | null>(null); // New state for free agents
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const userUUID = useUUID();

  const fetchData = useCallback(() => {
    if (userUUID) {
      fetch('https://ff-ranking-visualizer.azurewebsites.net/load-cached-starts', {
        headers: {
          'X-User-UUID': userUUID,
        },
      })
        .then((response) => {
          if (!response.ok) {
            throw new Error('Network response was not ok');
          }
          return response.json();
        })
        .then((data: DataDictionary) => {
          const leagueNames = data['league_names'];
          setData(leagueNames);
          const firstKey = Object.keys(leagueNames)[0];
          setSelectedTab(leagueNames[firstKey]);
          setLoading(false);
        })
        .catch((error) => {
          console.error('Error fetching data:', error);
          setError('Failed to load league names.');
          setLoading(false);
        });
    }
  }, [userUUID]);

  useEffect(() => {
    if (showTabs) {
      fetchData();
    }
  }, [showTabs, fetchData]);

  const handleTabChange = (leagueName: string) => {
    setSelectedTab(leagueName);
    setLoading(true); // Show loading state while fetching

    // Fetch specific league data
    fetch(`https://ff-ranking-visualizer.azurewebsites.net/load-league-data?league=${encodeURIComponent(leagueName)}`, {
      headers: {
        'X-User-UUID': userUUID,
      },
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error('Failed to fetch league data');
        }
        return response.json();
      })
      .then((data: Player[]) => {
        setLeagueData(data);

        // Fetch free agent data
        return fetch(`https://ff-ranking-visualizer.azurewebsites.net/load-free-agent-data?league=${encodeURIComponent(leagueName)}`, {
          headers: {
            'X-User-UUID': userUUID,
          },
        });
      })
      .then((response) => {
        if (!response.ok) {
          throw new Error('Failed to fetch free agent data');
        }
        return response.json();
      })
      .then((freeAgentData: FreeAgent[]) => {
        setFreeAgentData(freeAgentData);
        setLoading(false);
      })
      .catch((error) => {
        console.error('Error:', error);
        setError('Failed to load league or free agent data.');
        setLoading(false);
      });
  };

  if (!showTabs) {
    return null;
  }

  if (loading) {
    return <div>Loading...</div>;
  }

  if (error) {
    return <div>{error}</div>;
  }

  return (
    <div>
      <div className="tabs">
        {Object.keys(data).map((key) => (
          <button
            key={data[key]}
            onClick={() => handleTabChange(data[key])}
            className={selectedTab === data[key] ? 'active' : ''}
          >
            {data[key]}
          </button>
        ))}
      </div>
      <div className="content">
        {selectedTab && leagueData && (
          <PlayerTable data={leagueData} />
        )}
        {freeAgentData && (
          <div className="free-agent-section">
            <h2>Free Agents</h2>
            <table className="free-agent-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Position</th>
                  <th>Vegas Projected Score</th>
                </tr>
              </thead>
              <tbody>
                {freeAgentData.map((agent) => (
                  <tr key={agent.NAME}>
                    <td>{agent.NAME}</td>
                    <td>{agent.POS}</td>
                    <td>{agent.VEGAS}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

export default DynamicTabs;
