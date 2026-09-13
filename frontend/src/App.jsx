import { BrowserRouter, Route, Routes } from 'react-router-dom'
import AppShell from './AppShell.jsx'
import MarketWatch from './pages/MarketWatch.jsx'
import Strategies from './pages/Strategies.jsx'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<MarketWatch />} />
          <Route path="strategies" element={<Strategies />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
