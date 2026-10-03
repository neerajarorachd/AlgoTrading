import { BrowserRouter, Route, Routes } from 'react-router-dom'
import AppShell from './AppShell.jsx'
import Backtests from './pages/Backtests.jsx'
import EngineSettings from './pages/EngineSettings.jsx'
import MarketWatch from './pages/MarketWatch.jsx'
import OccurrenceBacktest from './pages/OccurrenceBacktest.jsx'
import RecommendationSystems from './pages/RecommendationSystems.jsx'
import Recommendations from './pages/Recommendations.jsx'
import Strategies from './pages/Strategies.jsx'
import Watchlists from './pages/Watchlists.jsx'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<MarketWatch />} />
          <Route path="strategies" element={<Strategies />} />
          <Route path="watchlists" element={<Watchlists />} />
          <Route path="backtests" element={<Backtests />} />
          <Route path="occurrence-backtest" element={<OccurrenceBacktest />} />
          <Route path="recommendations" element={<Recommendations />} />
          <Route path="recommendation-systems" element={<RecommendationSystems />} />
          <Route path="engine-settings" element={<EngineSettings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
