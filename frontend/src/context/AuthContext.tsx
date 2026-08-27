import { createContext, useContext, useState, type ReactNode } from 'react'

export interface User {
  name: string
  email: string
  businessName: string
  initials: string
}

interface AuthContextValue {
  user: User | null
  isAuthenticated: boolean
  login: (email: string, pass: string) => Promise<void>
  signup: (businessName: string, email: string, pass: string) => Promise<void>
  logout: () => void
  demoUser: { email: string; pass: string }
}

const DEMO_USER: User = {
  name: 'Meera Chandran',
  email: 'merchant@restockai.com',
  businessName: 'Chennai Central Store',
  initials: 'MC',
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(() => {
    try {
      const saved = localStorage.getItem('restock_auth_user')
      return saved ? JSON.parse(saved) : DEMO_USER
    } catch {
      return DEMO_USER
    }
  })

  const login = async (email: string, _pass: string) => {
    await new Promise((res) => setTimeout(res, 500))
    const u: User = {
      name: email === DEMO_USER.email ? DEMO_USER.name : email.split('@')[0] || 'Merchant',
      email: email,
      businessName: DEMO_USER.businessName,
      initials: (email[0] || 'M').toUpperCase(),
    }
    setUser(u)
    localStorage.setItem('restock_auth_user', JSON.stringify(u))
  }

  const signup = async (businessName: string, email: string, _pass: string) => {
    await new Promise((res) => setTimeout(res, 500))
    const u: User = {
      name: email.split('@')[0] || 'Merchant',
      email: email,
      businessName: businessName || 'My Store',
      initials: (businessName[0] || 'M').toUpperCase(),
    }
    setUser(u)
    localStorage.setItem('restock_auth_user', JSON.stringify(u))
  }

  const logout = () => {
    setUser(null)
    localStorage.removeItem('restock_auth_user')
  }

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: !!user,
        login,
        signup,
        logout,
        demoUser: { email: 'merchant@restockai.com', pass: 'demo1234' },
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return ctx
}
