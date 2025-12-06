import './Navbar.css';

export default function Navbar({ onMenuClick, title }) {
  return (
    <nav className="navbar">
      <button className="navbar-menu-btn" onClick={onMenuClick} aria-label="Toggle menu">
        <span className="hamburger-icon">
          <span></span>
          <span></span>
          <span></span>
        </span>
      </button>
      <h1 className="navbar-title">{title || 'LLM Council'}</h1>
    </nav>
  );
}
