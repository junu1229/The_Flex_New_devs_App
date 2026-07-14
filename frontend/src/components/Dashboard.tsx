import React, { useEffect, useState } from 'react';
import { SecureAPI, type DashboardProperty } from '../lib/secureApi';
import { RevenueSummary } from './RevenueSummary';

const DEFAULT_REPORT_YEAR = 2024;
const DEFAULT_REPORT_MONTH = 3;

const Dashboard: React.FC = () => {
  const [properties, setProperties] = useState<DashboardProperty[]>([]);
  const [selectedProperty, setSelectedProperty] = useState('');
  const [propertiesLoading, setPropertiesLoading] = useState(true);
  const [propertiesError, setPropertiesError] = useState('');

  useEffect(() => {
    let cancelled = false;

    const loadProperties = async () => {
      try {
        const response = await SecureAPI.getDashboardProperties();
        if (cancelled) return;

        setProperties(response.properties);
        setSelectedProperty(response.properties[0]?.id ?? '');
      } catch (error) {
        if (cancelled) return;

        console.error(error);
        setPropertiesError('Failed to load properties');
      } finally {
        if (!cancelled) setPropertiesLoading(false);
      }
    };

    void loadProperties();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="p-4 lg:p-6 min-h-full">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-2xl font-bold mb-6 text-gray-900">Property Management Dashboard</h1>

        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 lg:p-6">
          <div className="mb-6">
            <div className="flex flex-col sm:flex-row sm:justify-between sm:items-start gap-4">
              <div>
                <h2 className="text-lg lg:text-xl font-medium text-gray-900 mb-2">Revenue Overview</h2>
                <p className="text-sm lg:text-base text-gray-600">
                  March 2024 performance insights for your properties
                </p>
              </div>

              <div className="flex flex-col sm:items-end">
                <label htmlFor="dashboard-property" className="text-xs font-medium text-gray-700">
                  Select Property
                </label>
                <select
                  id="dashboard-property"
                  value={selectedProperty}
                  onChange={(event) => setSelectedProperty(event.target.value)}
                  disabled={propertiesLoading || properties.length === 0}
                  className="block w-full sm:w-auto min-w-[200px] px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 text-sm"
                >
                  {propertiesLoading ? <option value="">Loading properties...</option> : null}
                  {!propertiesLoading && properties.length === 0 ? (
                    <option value="">No properties available</option>
                  ) : null}
                  {properties.map((property) => (
                    <option key={property.id} value={property.id}>
                      {property.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          <div className="space-y-6">
            {propertiesError ? (
              <div className="p-4 text-red-500 bg-red-50 rounded-lg">{propertiesError}</div>
            ) : selectedProperty ? (
              <RevenueSummary
                propertyId={selectedProperty}
                year={DEFAULT_REPORT_YEAR}
                month={DEFAULT_REPORT_MONTH}
              />
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
