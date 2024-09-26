import React from 'react';

// Define the type for your player data
interface Player {
  NAME: string;
  POS: string;
  POS_RANK: string;
  FLEX?: string;
  PID?: string; // Add this field for player headshot
  TEAM?: string; // Add this field for team logo
  VEGAS?: string; // Projected vegas score, might not be there for defenses and kickers
  MATCHUP_RATING?: string;
  TEAM_NAME?: string;
}

interface PlayerTableProps {
  data: Player[];
}

// Define tier colors with string keys
const tierColors: { [key: string]: string } = {
  '1': '#004d00', // Dark Green
  '2': '#006400', // Medium Green
  '3': '#4CAF50', // Light Green
  '4': '#8BC34A', // Yellow-Green
  '5': '#FFC107', // Yellow
  '6': '#FFB74D', // Light Orange
  '7': '#FF9800', // Orange
  '8': '#FF5722', // Light Red
  '9': '#F44336', // Red
  '10': '#B71C1C', // Dark Red for Tier 10 and worse
};

const getColorForRank = (rank: string) => {
  const rankValue = parseInt(rank, 10);

  if (isNaN(rankValue) || rankValue > 10) {
    return '#B71C1C'; // Dark Red for 'UNRANKED' or ranks beyond 10
  }

  return tierColors[rankValue.toString()] || '#B71C1C'; // Default to Dark Red if tier is undefined
};

const PlayerTable: React.FC<PlayerTableProps> = ({ data }) => {
  return (
    <div style={{ overflowX: 'auto', margin: '20px' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'Arial, sans-serif' }}>
        <thead style={{ backgroundColor: '#f4f4f4', textAlign: 'left' }}>
          <tr>
            <th style={{ padding: '12px', borderBottom: '2px solid #ddd', width: '6%', textAlign: 'center', borderRight: '1px solid #ddd' }}>Position</th>
            <th style={{ padding: '12px', borderBottom: '2px solid #ddd', width: '17%', textAlign: 'center', borderRight: '1px solid #ddd' }}>Name</th>
            <th style={{ padding: '12px', borderBottom: '2px solid #ddd', width: '15%', textAlign: 'center', borderRight: '1px solid #ddd' }}>Positional Ranking</th>
            <th style={{ padding: '12px', borderBottom: '2px solid #ddd', width: '15%', textAlign: 'center', borderRight: '1px solid #ddd' }}>Flex Ranking</th>
            <th style={{ padding: '12px', borderBottom: '2px solid #ddd', width: '10%', textAlign: 'center', borderRight: '1px solid #ddd' }}>Vegas Projected Points</th>
            <th style={{ padding: '12px', borderBottom: '2px solid #ddd', width: '15%', textAlign: 'center', borderRight: '1px solid #ddd' }}>Matchup Rating</th>
            
          </tr>
        </thead>
        <tbody>
          {data.map((player, index) => (
            <tr key={index} style={{ backgroundColor: index % 2 === 0 ? '#fff' : '#f9f9f9' }}>
              <td style={{ padding: '12px', borderBottom: '1px solid #ddd', borderRight: '1px solid #ddd', textAlign: 'left', verticalAlign: 'middle' }}>{player.POS}</td>
              <td style={{ position: 'relative', padding: '12px', borderBottom: '1px solid #ddd', borderRight: '1px solid #ddd', verticalAlign: 'middle', display: 'flex', alignItems: 'center' }}>
              {player.PID && (
                <div style={{ position: 'relative', width: '75px', height: '50px' }}> {/* Wrapper for the images */}
                    <img
                      src={"https://sleepercdn.com/content/nfl/players/" + player.PID + ".jpg"}
                      alt={`${player.NAME} logo`}
                      style={{ width: '75px', height: '50px', borderRadius: '50%', marginRight: '10px' }}
                    />
                  
                  {player.TEAM_NAME && (
                    <img
                      src={"https://sleepercdn.com/images/team_logos/nfl/" + player.TEAM_NAME.toLowerCase() + ".png"}
                      alt={`${player.TEAM_NAME} logo`}
                      style={{
                        position: 'absolute',
                        width: '20px',
                        height: '20px',
                        borderRadius: '50%',
                        bottom: '-3px',
                        right: '10px', // Position in the bottom-right corner of the wrapper
                      }}
                    />
                  )}
                </div>
              )}
                {player.TEAM && (
                  <img
                    src={"https://sleepercdn.com/images/team_logos/nfl/" + player.TEAM.toLowerCase() + ".png"}
                    alt={`${player.NAME} logo`}
                    style={{
                      width: '50px',
                      height: '50px',
                      borderRadius: '60%',
                      marginRight: '25px',
                      marginLeft: '10px',
                    }}
                  />
                )}
                <span style={{ marginRight: '10px' }}>{player.NAME}</span>
              </td>
              <td style={{ padding: '12px', borderBottom: '1px solid #ddd', borderRight: '1px solid #ddd', textAlign: 'left', verticalAlign: 'middle' }}>
                <div style={{
                  display: 'inline-block',
                  width: '20px',
                  height: '20px',
                  backgroundColor: getColorForRank(player.POS_RANK),
                  borderRadius: '50%',
                  marginRight: '8px',
                  verticalAlign: 'middle'
                }} />
                {player.POS_RANK !== "Unranked" ? "Tier " + player.POS_RANK : player.POS_RANK}
              </td>
              <td style={{ padding: '12px', borderBottom: '1px solid #ddd', borderRight: '1px solid #ddd', textAlign: 'left', verticalAlign: 'middle' }}>
                <div style={{
                  display: 'inline-block',
                  width: '20px',
                  height: '20px',
                  backgroundColor: player.FLEX ? getColorForRank(player.FLEX) : '#B71C1C', // Use dark red if FLEX is missing
                  borderRadius: '50%',
                  marginRight: '8px',
                  verticalAlign: 'middle'
                }} />
                {player.FLEX ? "Tier " + player.FLEX : 'Unranked'}
              </td>
              <td style={{padding: '12px', borderBottom: '1px solid #ddd', borderRight: '1px solid #ddd', textAlign: 'left', verticalAlign: 'middle' }}>{player.VEGAS + " Points"}</td>
              <td style={{padding: '12px', borderBottom: '1px solid #ddd', textAlign: 'center', verticalAlign: 'middle' }}>
                {Array.from({ length: parseInt(player.MATCHUP_RATING ?? '0') }, (_, index) => (
                  <span key={index}>⭐</span> // Display stars based on the MATCHUP_RATING
                ))}
                
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default PlayerTable;
