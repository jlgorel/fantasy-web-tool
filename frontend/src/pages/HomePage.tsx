import React, { useState, useEffect } from 'react';
import { useUUID } from '../context/UUIDContext'; // Import the hook
import DynamicTabs from '../components/LeagueTabs';

const HomePage: React.FC = () => {
  const [name, setName] = useState<string>('');
  const [showTabs, setShowTabs] = useState<boolean>(false);
  const [showInstructions, setShowInstructions] = useState<boolean>(true); // State for instructions visibility
  const [runtime, setRuntime] = useState('');

  const userUUID = useUUID(); // This should now work

  // Function to load the last run info from the API
  const loadLastRunInfo = async () => {
    try {
      const response = await fetch('https://ff-ranking-visualizer.azurewebsites.net/load-last-run-info'); // Adjust this URL as needed
      const runtime = await response.json(); // Parse the response as JSON
      setRuntime(runtime); // Assuming the API returns the runtime directly
    } catch (error) {
      console.error('Error fetching runtime info:', error);
    }
  };

  useEffect(() => {
    loadLastRunInfo();
  }, []);

  const handleSaveClick = async () => {
    try {
      if (userUUID) {
        setShowTabs(false);
        console.log("trying to load users for username:", name);
        
        // Set instructions visibility to false when button is clicked
        setShowInstructions(false); 

        const response = await fetch('https://ff-ranking-visualizer.azurewebsites.net/load-sleeper-info', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-User-UUID': userUUID,
          },
          body: JSON.stringify({ name }),
        });

        if (response.ok) {
          const result = await response.json();
          console.log('Sleeper league data loaded for user:', result);
          setName(''); // Clear the textbox after saving
          setShowTabs(true);
        } else {
          console.error('Failed to get data for user');
        }
      }
    } catch (error) {
      console.error('Error:', error);
    }
  };

  return (
    <div className="App">
      <div className="header-container" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: '20px' }}>
        <img src={`${process.env.PUBLIC_URL}/AmericanFootball.png`} alt="Football" style={{ width: '100px', marginRight: '10px' }} />
        <h1 style={{ fontSize: '2rem', textAlign: 'center' }}>Fantasy Football Team Visualizer</h1>
        <img src={`${process.env.PUBLIC_URL}/AmericanFootball.png`} alt="Football" style={{ width: '100px', marginLeft: '10px' }} />
        <div style={{ position: 'absolute', top: '10px', right: '10px', fontSize: '16px', backgroundColor: 'rgba(255, 255, 255, 0.8)', padding: '5px', borderRadius: '5px' }}>
          Data last updated at: {runtime || "Loading..."} 
        </div>
      </div>
      <p style={{ textAlign: 'center', fontSize: '25px' }}>Enter your Sleeper username to load your fantasy football teams and visualize your lineups!</p>

      <div style={{ display: 'flex', alignItems: 'flex-start', marginTop: '20px' }}>
        {/* Left side for instructions */}
        {showInstructions && (
          <div style={{ flex: 1, textAlign: 'left'}}>
            <b style={{ fontSize: '25px', marginLeft: '250px'}}>How To Use:</b>
            <p style={{ fontSize: '15px', maxWidth: '400px', marginLeft: '150px'  }}>
              The app will load in all rosters that we have projections for. Rosters with IDP are currently not supported.
              Boris Chen tiers are used to build an "ideal" starting lineup. During ties for positional ranks, the higher flex rank is chosen. Leagues with non-standard scoring get rounded to the closest tier on his site.
              On the right is also a vegas-projected score that utilizes sportsbook betting lines, in addition to your leagues specific settings, to project what each player will score that week.
              Finally, the matchup rating is listed on the far right: 5 stars is a great matchup they should excel in, 1 star is a awful matchup they might get clamped in.
              Enjoy and please let me know about any bugs that you find!
            </p>
          </div>
        )}

        {showInstructions && (

        <div style={{ flex: 1, textAlign: 'center', marginTop: '100px', marginRight: '50px' }}>
          <div style={{ display: 'flex', justifyContent: 'center', marginTop: '20px' }}>
            <div className="input-section">
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Enter sleeper username"
              />
            </div>
            <button onClick={handleSaveClick} style={{ height: '35px', marginTop: '20px' }}>Load Teams</button>
          </div>
        </div>
      )}
      </div>

      {/* Center button when paragraph is hidden */}
      {!showInstructions && (
        <div style={{ display: 'flex', justifyContent: 'center', marginTop: '20px' }}>
          <div className="input-section">
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Enter sleeper username"
            />
          </div>
          <button style={{ height: '35px', marginTop: '20px' }} onClick={handleSaveClick}>Load Teams</button>
        </div>
      )}

      <DynamicTabs showTabs={showTabs}></DynamicTabs>
    </div>
  );
};

export default HomePage;
