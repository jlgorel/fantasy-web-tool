import React, { useState, useEffect, useCallback } from 'react';
import PlayerTable from './PlayerTable';
import { useUUID } from '../context/UUIDContext'; // Import the hook

// Define the type for your data
interface DataDictionary {
  [key: string]: any; // Replace `any` with a more specific type if known
}

interface Player {
  NAME: string;
  POS: string;
  POS_RANK: string;
  FLEX?: string;
}

interface DynamicTabsProps {
    showTabs: boolean; // Prop to control visibility
  }

const DynamicTabs: React.FC<DynamicTabsProps> = ({showTabs}) => {
  const [data, setData] = useState<DataDictionary>({});
  const [selectedTab, setSelectedTab] = useState<string | null>(null);
  const [leagueData, setLeagueData] = useState<Player[] | null>(null); // State for league-specific data
  const [loading, setLoading] = useState<boolean>(true); // Loading state
  const [error, setError] = useState<string | null>(null); // Error state

  const userUUID = useUUID()

  const fetchData = useCallback(() => {
    if(userUUID){
      // Fetch the league names from your API
      fetch('https://ff-ranking-visualizer.azurewebsites.net/load-cached-starts', {
        headers: {
          'X-User-UUID': userUUID,
        },
      })
        .then(response => {
          if (!response.ok) {
            throw new Error('Network response was not ok');
          }
          return response.json();
        })
        .then((data: DataDictionary) => {
          const leagueNames = data["league_names"];
          setData(leagueNames);
          // Set default tab if data exists
          const firstKey = Object.keys(leagueNames)[0];
          setSelectedTab(leagueNames[firstKey]);
          setLoading(false); // Set loading to false when data is loaded
        })
        .catch(error => {
          console.error('Error fetching data:', error);
          setError('Failed to load league names.'); // Set error message
          setLoading(false); // Set loading to false even if there's an error
        });
      }
    }, []);
  

  // Fetch data on initial render
  useEffect(() => {
    if (showTabs) {
      fetchData();
    }
  }, [showTabs, fetchData]);

  const handleTabChange = (leagueName: string) => {
    setSelectedTab(leagueName);

    // Fetch specific data for the selected league
    fetch(`https://ff-ranking-visualizer.azurewebsites.net/load-league-data?league=${encodeURIComponent(leagueName)}`, {
      headers: {
        'X-User-UUID': userUUID,
      },
    })
      .then(response => {
        if (!response.ok) {
          throw new Error('Network response was not ok');
        }
        return response.json();
      })
      .then((data: Player[]) => {
        setLeagueData(data);
      })
      .catch(error => {
        console.error('Error fetching league data:', error);
        setError('Failed to load league data.');
      });
  };

  if (!showTabs) {
    return null; // Hide component if showTabs is false
  }

  if (loading) {
    return <div>Loading...</div>; // Display loading state
  }

  if (error) {
    return <div>{error}</div>; // Display error state
  }

  return (
    <div>
      <div className="tabs">
        {Object.keys(data).map(key => (
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
      </div>
    </div>
  );
};

export default DynamicTabs;
