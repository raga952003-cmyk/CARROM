import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import App from './App.tsx';
import './index.css';
import {ErrorBoundary} from './components/common/ErrorBoundary';
import {NotificationProvider} from './context/NotificationContext';

// Order matters. The boundary is outermost so it still has something to render
// if the notification provider itself is what breaks; the provider sits above
// App so every screen can report, and so the global rejection listener is
// installed before any screen starts making requests.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <NotificationProvider>
        <App />
      </NotificationProvider>
    </ErrorBoundary>
  </StrictMode>,
);
