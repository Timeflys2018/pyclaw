import { AuthProvider, useAuth } from './context/AuthContext'
import Login from './pages/Login'
import Chat from './pages/Chat'

function Router() {
  const { token } = useAuth()
  return token ? <Chat /> : <Login />
}

export default function App() {
  return (
    <AuthProvider>
      <Router />
    </AuthProvider>
  )
}
