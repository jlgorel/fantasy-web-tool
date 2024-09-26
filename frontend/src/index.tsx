import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import './styles/App.css';
import App from './App';
import reportWebVitals from './reportWebVitals';
import { UUIDProvider } from './context/UUIDContext'; // Ensure this import is correct

const root = ReactDOM.createRoot(
  document.getElementById('root') as HTMLElement
);

root.render(
  <React.StrictMode>
    <UUIDProvider> {/* This should wrap App */}
      <App />
    </UUIDProvider>
  </React.StrictMode>
);

reportWebVitals();
