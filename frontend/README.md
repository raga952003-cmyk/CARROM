# Carrom Tournament Management - Frontend

## React + TypeScript + Vite Frontend

This is the frontend application that connects to the Python (FastAPI) backend.

---

## 🚀 Quick Start

### Prerequisites:
- Node.js 18+ installed
- Backend server running on http://localhost:8000

### Installation:
```bash
# Install dependencies
npm install

# Start development server
npm run dev
```

**Access**: http://localhost:5173

---

## 📁 Project Structure

```
frontend/
├── src/
│   ├── components/      # React components
│   │   ├── admin/      # Admin dashboard components
│   │   ├── player/     # Player dashboard components
│   │   └── common/     # Shared components
│   ├── context/        # React Context (state management)
│   ├── services/       # API service layer
│   │   ├── authService.ts
│   │   └── tournamentService.ts
│   ├── utils/          # Utilities
│   │   ├── apiClient.ts
│   │   └── tournamentEngine.ts
│   ├── types/          # TypeScript type definitions
│   ├── data/           # Sample/initial data
│   └── App.tsx         # Main app component
├── index.html          # HTML entry point
├── vite.config.ts      # Vite configuration
├── tsconfig.json       # TypeScript configuration
├── .env                # Environment variables
└── package.json        # Dependencies
```

---

## 🔧 Configuration

### Environment Variables (`.env`):
```env
VITE_API_URL=http://localhost:8000/api
```

**Important**: 
- The frontend expects the backend to run on port 8000
- Make sure backend is started before running frontend
- All API calls will go through the Python backend

---

## 📡 API Integration

The frontend communicates with the Python backend via REST API:

```typescript
// Example: Login
import { authService } from './services/authService';

const response = await authService.login({
  email: 'admin@carrom.com',
  password: 'admin123',
  role: 'admin'
});
```

All API calls are handled through:
- `src/utils/apiClient.ts` - HTTP client with JWT auth
- `src/services/authService.ts` - Authentication
- `src/services/tournamentService.ts` - Tournament operations

---

## 🏗️ Available Scripts

```bash
# Development
npm run dev          # Start dev server (port 5173)

# Production
npm run build        # Build for production
npm run preview      # Preview production build

# Linting
npm run lint         # TypeScript type checking
```

---

## 🔐 Authentication Flow

1. User enters credentials on login page
2. Frontend sends POST to `/api/auth/login`
3. Backend validates and returns JWT token
4. Frontend stores token in localStorage
5. All subsequent requests include token in headers
6. Backend validates token and returns data

---

## 🎨 Tech Stack

- **React 19** - UI framework
- **TypeScript** - Type safety
- **Vite** - Build tool & dev server
- **TailwindCSS** - Styling
- **Lucide Icons** - Icon library
- **Motion** - Animations
- **Canvas Confetti** - Celebrations

---

## 📦 Dependencies

See `package.json` for full list. Key dependencies:
- react, react-dom
- typescript
- vite
- @tailwindcss/vite
- lucide-react
- motion

---

## 🔗 Backend Connection

The frontend expects these backend endpoints:

### Auth:
- POST `/api/auth/signup`
- POST `/api/auth/login`
- GET `/api/auth/me`

### Tournaments:
- GET `/api/tournaments`
- POST `/api/tournaments`
- PUT `/api/tournaments/{id}`
- DELETE `/api/tournaments/{id}`

### Matches:
- GET `/api/tournaments/{id}/matches`
- PUT `/api/matches/{id}`

### Players:
- GET `/api/players`
- POST `/api/players`

See backend API docs at: http://localhost:8000/api/docs

---

## 🐛 Troubleshooting

### "Cannot connect to backend"
**Solution**: 
1. Make sure backend is running: `cd ../backend && python run.py`
2. Check VITE_API_URL in `.env`
3. Verify CORS is enabled in backend

### "Module not found"
**Solution**: 
```bash
npm install
```

### "Port 5173 already in use"
**Solution**:
```bash
# Kill process or use different port
npm run dev -- --port 3000
```

---

## 🚀 Deployment

### Build for production:
```bash
npm run build
```

Output will be in `dist/` folder.

### Deploy to:
- **Vercel**: `vercel deploy`
- **Netlify**: Drag & drop `dist/` folder
- **AWS S3**: Upload `dist/` folder

**Don't forget to:**
1. Update `VITE_API_URL` to production backend URL
2. Configure CORS in production backend
3. Use HTTPS for production

---

## 📝 Development Notes

### Adding New Features:
1. Create component in `src/components/`
2. Add API call in `src/services/`
3. Update types in `src/types/`
4. Test locally

### API Client Usage:
```typescript
import { apiClient } from '../utils/apiClient';

// GET request
const data = await apiClient.get('/tournaments');

// POST request
const result = await apiClient.post('/tournaments', {
  name: 'My Tournament'
});
```

---

**Status**: ✅ Integrated with Python Backend  
**Port**: 5173 (default)  
**Backend**: http://localhost:8000
