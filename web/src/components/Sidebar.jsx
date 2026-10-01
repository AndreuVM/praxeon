import React from 'react';
import TopNav from './TopNav';

// For backward compatibility, Sidebar now renders the horizontal TopNav
export default function Sidebar(props) {
  return <TopNav {...props} />;
}
