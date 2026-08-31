import React from 'react'
import ReactDOM from 'react-dom/client'

function App() {
  return (
    <main style={{ fontFamily: 'sans-serif', padding: '2rem' }}>
      <h1>DC Build Planner</h1>
      <p>Scaffold ready for Task 1.</p>
    </main>
  )
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
