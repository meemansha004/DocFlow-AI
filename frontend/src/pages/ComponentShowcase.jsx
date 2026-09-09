import React, { useState } from 'react';
import { Plus, Search, FileText, File, AlertCircle } from 'lucide-react';

import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import Badge from '../components/ui/Badge';
import Input from '../components/ui/Input';
import Textarea from '../components/ui/Textarea';
import Modal from '../components/ui/Modal';
import Dropdown from '../components/ui/Dropdown';
import CollapsibleSection from '../components/ui/CollapsibleSection';

const ComponentShowcase = () => {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [dropdownValue, setDropdownValue] = useState('');

  const stages = [
    { label: 'Intake', value: 'intake' },
    { label: 'Requirements', value: 'requirements' },
    { label: 'Design', value: 'design' },
    { label: 'Development', value: 'development' },
  ];

  return (
    <div className="min-h-screen bg-background text-gray-200 p-8 space-y-12">
      <div>
        <h1 className="text-3xl font-bold bg-gemini-gradient bg-clip-text text-transparent mb-2">Component Showcase</h1>
        <p className="text-gray-400">Development-only route to verify Phase 1 primitives.</p>
      </div>

      <section className="space-y-4">
        <h2 className="text-xl font-semibold border-b border-border pb-2">1. Buttons</h2>
        <div className="flex flex-wrap gap-4 items-center">
          <Button variant="primary">Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="danger">Danger</Button>
          <Button variant="primary" disabled>Disabled</Button>
          <Button variant="primary" icon={Plus}>With Icon</Button>
          <Button variant="primary" loading>Loading</Button>
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="text-xl font-semibold border-b border-border pb-2">2. Cards</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <Card 
            title="Basic Card" 
            description="A simple card with title and description."
          >
            <p className="text-gray-300">This is the content of the basic card.</p>
          </Card>
          
          <Card 
            title="Project Workspace" 
            description="Manage your documents and chat."
            icon={FileText}
            hoverable
            footer={
              <div className="flex justify-end">
                <Button size="sm">Open</Button>
              </div>
            }
          >
            <div className="flex gap-2 mt-2">
              <Badge variant="active">Active</Badge>
              <Badge>Internal</Badge>
            </div>
          </Card>

          <Card onClick={() => alert('Clicked!')}>
            <div className="text-center py-6 text-gray-400">
              <Plus size={32} className="mx-auto mb-2 text-primary" />
              <p>Clickable Card</p>
            </div>
          </Card>
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="text-xl font-semibold border-b border-border pb-2">3. Badges</h2>
        <div className="flex flex-wrap gap-3">
          <Badge variant="active">Active</Badge>
          <Badge variant="inactive">Inactive</Badge>
          <Badge variant="success">Success</Badge>
          <Badge variant="warning">Warning</Badge>
          <Badge variant="danger">Confidential</Badge>
          <Badge variant="neutral">PDF</Badge>
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="text-xl font-semibold border-b border-border pb-2">4. Inputs</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-2xl">
          <Input label="Normal Input" placeholder="Enter text..." />
          <Input label="Required Input" placeholder="Enter text..." required />
          <Input label="With Icon" placeholder="Search..." icon={Search} />
          <Input label="Disabled Input" placeholder="Cannot edit" disabled />
          <Input label="Error State" placeholder="Enter text..." error="This field is required." />
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="text-xl font-semibold border-b border-border pb-2">5. Textareas</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-2xl">
          <Textarea label="Normal Textarea" placeholder="Describe your project..." />
          <Textarea label="Error State" placeholder="Type..." error="Description is too short." />
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="text-xl font-semibold border-b border-border pb-2">6. Modal</h2>
        <Button onClick={() => setIsModalOpen(true)}>Open Modal</Button>
        <Modal
          open={isModalOpen}
          onClose={() => setIsModalOpen(false)}
          title="Create New Project"
          description="Set up a new workspace for your documents."
          footer={
            <>
              <Button variant="ghost" onClick={() => setIsModalOpen(false)}>Cancel</Button>
              <Button onClick={() => setIsModalOpen(false)}>Create Project</Button>
            </>
          }
        >
          <div className="space-y-4">
            <Input label="Project Name" placeholder="e.g. Q3 Financial Report" />
            <Textarea label="Description" placeholder="What is this project about?" />
          </div>
        </Modal>
      </section>

      <section className="space-y-4">
        <h2 className="text-xl font-semibold border-b border-border pb-2">7. Dropdown</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 max-w-3xl">
          <Dropdown 
            label="Project Stage" 
            options={stages} 
            value={dropdownValue} 
            onChange={setDropdownValue} 
          />
          <Dropdown 
            label="Disabled" 
            options={stages} 
            disabled 
          />
          <Dropdown 
            label="With Error" 
            options={stages} 
            error="Please select a stage" 
          />
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="text-xl font-semibold border-b border-border pb-2">8. Collapsible Section</h2>
        <div className="max-w-2xl space-y-4">
          <CollapsibleSection title="Requirements" count={2} badgeVariant="warning" defaultExpanded>
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-sm text-gray-300">
                <File size={16} className="text-red-400" />
                <span>PRD.pdf</span>
              </div>
              <div className="flex items-center gap-2 text-sm text-gray-300">
                <File size={16} className="text-blue-400" />
                <span>Business_Requirements.docx</span>
              </div>
            </div>
          </CollapsibleSection>
          
          <CollapsibleSection title="Design" count={1} badgeVariant="neutral">
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-sm text-gray-300">
                <File size={16} className="text-red-400" />
                <span>System_Architecture.pdf</span>
              </div>
            </div>
          </CollapsibleSection>
        </div>
      </section>
    </div>
  );
};

export default ComponentShowcase;
